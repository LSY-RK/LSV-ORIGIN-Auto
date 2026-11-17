import sys
import os
import glob
import re
from collections import defaultdict

import pandas as pd
import originpro as op
import OriginExt as oe
import PyQt5
qt_plugin_dir = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")
os.environ["QT_PLUGIN_PATH"] = qt_plugin_dir
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(
    qt_plugin_dir, "platforms"
)

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap

# ==========================================
# 1. 기존의 Origin 데이터 처리 함수들 (그대로 유지)
# ==========================================
REF_DICT = {
    "Hg/HgO": 0.098,
    "Ag/AgCl (3.5M KCl)": 0.205,
}

def connect_origin():
    """Attach to an existing Origin instance, or start one if needed."""
    import originpro.config as opc

    try:
        op.attach()
    except Exception as attach_error:
        message = str(attach_error).lower()
        is_rpc_error = "rpc" in message or "원격 프로시저" in message

        if is_rpc_error and opc.oext and "_app" in vars(opc.po):
            # Drop only the dead COM proxy; do not terminate an existing process here.
            opc.po._app = None

        try:
            op.set_show(False)
        except Exception as start_error:
            raise RuntimeError(
                "Origin에 연결할 수 없습니다. Origin을 실행한 뒤 다시 시도하세요."
            ) from start_error

    print("Origin 연결 완료")


def force_xy_designations(wks, pattern):
    """Set and verify the requested repeating column designation pattern."""
    ncols = wks.cols
    if ncols <= 0 or ncols % len(pattern) != 0:
        raise RuntimeError(
            f"열 수와 패턴 길이가 맞지 않습니다: 열={ncols}, 패턴={pattern}"
        )

    expected = pattern * (ncols // len(pattern))
    codes = {"X": oe.COLTYPE_X, "Y": oe.COLTYPE_Y}

    for index, axis in enumerate(expected):
        wks.obj.Columns(index).SetType(codes[axis])

    actual = [wks.obj.Columns(index).GetType() for index in range(ncols)]
    wanted = [codes[axis] for axis in expected]
    if actual != wanted:
        raise RuntimeError(
            f"열 속성 검증 실패: {wks.lt_range()}\n"
            f"기대: {wanted}\n실제: {actual}"
        )

    print(f"  ✅ {wks.lt_range()}: {expected} 지정 및 검증 완료")


def create_sheet(df, sheet_name, pattern):
    """Create a worksheet, populate labels/data and set its X/Y pattern."""
    wks = op.new_sheet("w", sheet_name)
    if wks is None:
        raise RuntimeError(f"워크시트 생성 실패: {sheet_name}")

    wks.name = sheet_name
    wks.from_df(df)
    for index, labels in enumerate(df.columns):
        wks.set_label(index, str(labels[0]), "L")
        wks.set_label(index, str(labels[1]), "U")
        wks.set_label(index, str(labels[2]), "C")

    force_xy_designations(wks, pattern)
    return wks


def read_mpt_table(file):
    """Read an EC-Lab MPT table using its declared header length."""
    with open(file, encoding="latin1") as mpt_file:
        for line in mpt_file:
            match = re.match(r"^\s*Nb header lines\s*:\s*(\d+)", line)
            if match:
                header_lines = int(match.group(1))
                break
        else:
            raise ValueError(f"MPT 파일에서 헤더 줄 수를 찾을 수 없습니다: {file}")

    return pd.read_csv(
        file,
        sep=r"\s+",
        skiprows=header_lines,
        header=None,
        encoding="latin1",
    )


def read_nyquist(file, original_fname):
    """Read Real Z from column B and -Imaginary Z from column C."""
    raw = read_mpt_table(file)
    if raw.shape[1] < 3:
        raise ValueError(
            f"Nyquist 데이터에 최소 3열이 필요합니다. 현재: {raw.shape[1]}열"
        )

    real_z = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    minus_imag_z = pd.to_numeric(raw.iloc[:, 2], errors="coerce")
    valid = real_z.notna() & minus_imag_z.notna()
    real_z = real_z[valid].reset_index(drop=True)
    minus_imag_z = minus_imag_z[valid].reset_index(drop=True)
    if real_z.empty:
        raise ValueError("유효한 Real Z / -Imaginary Z 데이터가 없습니다.")

    df = pd.concat([real_z, minus_imag_z], axis=1)
    df.columns = pd.MultiIndex.from_tuples([
        ("Real Z", "Ω", original_fname),
        ("-Imaginary Z", "Ω", original_fname),
    ])
    return df


def extract_rs(df_nyquist):
    """Return Real Z at the measured point where -Imaginary Z is closest to zero."""
    min_position = int(df_nyquist.iloc[:, 1].abs().to_numpy().argmin())
    return float(df_nyquist.iloc[min_position, 0])


def read_lsv(file, original_fname, rs):
    """Read LSV data, discard two startup rows, and calculate RHE/iR-free columns."""
    raw = read_mpt_table(file)
    if raw.shape[1] < 8:
        raise ValueError(
            f"LSV 데이터에 최소 8열이 필요합니다. 현재: {raw.shape[1]}열"
        )
    if len(raw) <= 2:
        raise ValueError("LSV 데이터에 초기 두 행을 제외한 측정값이 없습니다.")

    raw = raw.iloc[2:].reset_index(drop=True)
    potential = pd.to_numeric(raw.iloc[:, 6], errors="coerce")
    current = pd.to_numeric(raw.iloc[:, 7], errors="coerce")
    valid = potential.notna() & current.notna()
    potential = potential[valid].reset_index(drop=True)
    current = current[valid].reset_index(drop=True)
    if potential.empty:
        raise ValueError("유효한 전위/전류 데이터가 없습니다.")

    potential_rhe = potential + RHE_SHIFT
    current_density = current / ELECTRODE_AREA
    current_amp = current * 0.001
    potential_comp_rhe = potential - current_amp * rs + RHE_SHIFT
    rs_column = pd.Series([rs] + [float("nan")] * (len(potential) - 1))

    df = pd.concat(
        [
            potential,
            potential_rhe,
            current,
            current_density,
            rs_column,
            potential_comp_rhe,
            current_density,
        ],
        axis=1,
    )
    df.columns = pd.MultiIndex.from_tuples([
        (f"Potential vs. {CURRENT_REF}", "V", original_fname),
        ("Potential vs. RHE", "V vs.RHE", original_fname),
        ("Current", "mA", original_fname),
        ("Current Density", "mA cm⁻²", original_fname),
        ("Series Resistance (Rs)", "Ω", f"{original_fname} Rs"),
        (
            "Potential vs. RHE (iR-free)",
            "V vs.RHE",
            f"{original_fname} (Comp)",
        ),
        (
            "Current Density",
            "mA cm⁻²",
            f"{original_fname} (Comp)",
        ),
    ])
    return df


def process_mpt_final():
    if not os.path.isdir(target_folder):
        raise FileNotFoundError(f"폴더가 없습니다: {target_folder}")
    if ELECTRODE_AREA <= 0:
        raise ValueError("전극 면적은 0보다 커야 합니다.")

    mpt_files = sorted(glob.glob(os.path.join(target_folder, "*.mpt")))
    if not mpt_files:
        print("mpt 파일을 찾을 수 없습니다.")
        return

    lsv_groups = defaultdict(list)
    nyquist_files = []
    for file in mpt_files:
        fname = os.path.splitext(os.path.basename(file))[0]
        nyquist_match = re.fullmatch(r"(.+)_N", fname, flags=re.IGNORECASE)
        if nyquist_match:
            nyquist_files.append((nyquist_match.group(1), file, fname))
            continue

        scan_match = re.fullmatch(r"(.+)_(\d+)", fname)
        if scan_match:
            base_name = scan_match.group(1)
            scan_num = int(scan_match.group(2))
        else:
            base_name = fname
            scan_num = 0
        lsv_groups[base_name].append((scan_num, file, fname))

    rs_by_group = {}
    nyquist_data = []
    for base_name, file, fname in nyquist_files:
        group_key = base_name.casefold()
        if group_key in rs_by_group:
            raise ValueError(f"같은 그룹의 Nyquist가 중복됩니다: {base_name}")
        try:
            df_nyquist = read_nyquist(file, fname)
            rs = extract_rs(df_nyquist)
        except Exception as e:
            raise RuntimeError(f"Nyquist의 Rs 계산 실패: {fname}") from e

        rs_by_group[group_key] = rs
        nyquist_data.append((base_name, fname, df_nyquist))
        print(f"✅ {fname}: Rs = {rs:.6g} Ω")

    connect_origin()
    try:
        op.new()
        default_book = op.find_book()
        if default_book:
            default_book.destroy()

        created_sheets = 0
        failed_files = []

        for base_name, files_info in lsv_groups.items():
            sheet_name = f"LSV_{base_name}"
            group_key = base_name.casefold()
            rs = rs_by_group.get(group_key, 0.0)
            print(f"\n➡ 처리 중: {sheet_name}; Rs = {rs:.6g} Ω")

            scans = []
            for scan_num, file, fname in sorted(files_info, key=lambda item: item[0]):
                try:
                    df_scan = read_lsv(file, fname, rs)
                    scans.append(df_scan)
                    print(f"  읽기 완료: {fname} ({len(df_scan)}행)")
                except Exception as e:
                    failed_files.append(fname)
                    print(f"  [읽기 실패] {fname}: {e}")

            if not scans:
                print("  [건너뜀] 유효한 스캔이 없습니다.")
                continue

            create_sheet(
                pd.concat(scans, axis=1),
                sheet_name,
                pattern="XXYYYXY",
            )
            created_sheets += 1

        for base_name, fname, df_nyquist in nyquist_data:
            sheet_name = f"Nyquist_{base_name}"
            print(f"\n➡ 처리 중: {sheet_name}")
            create_sheet(df_nyquist, sheet_name, pattern="XY")
            created_sheets += 1

        if created_sheets == 0:
            print("\n저장할 데이터가 없습니다.")
            return

        op.save(save_path)
        print(f"\n✅ {created_sheets}개 시트 저장 완료!\n파일: {save_path}")
        if failed_files:
            print("\n읽기 실패로 제외된 파일:\n" + "\n".join(failed_files))
    finally:
        try:
            op.exit()
            print("Origin 종료 완료")
        except Exception as close_error:
            print(f"Origin 종료 요청 실패: {close_error}")




def process_mpt_final(target_folder, current_ref="Ag/AgCl (3.5M KCl)", ph_value=7.0, electrode_area=0.5, output_name="LSV_Origin_Modified.opju"):
    """
    기존 로직과 동일합니다. UI에서 폴더 경로를 받아 실행됩니다.
    """
    # ... (기존 process_mpt_final 내부 로직 그대로 사용) ...
    pass 


# ==========================================
# 2. GUI (캐릭터 창) 구현 부분
# ==========================================
class DragDropApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        # 드래그 앤 드롭 허용
        self.setAcceptDrops(True)
        self.setWindowTitle("LSV Origin 자동 변환기")
        self.resize(600, 650)
        self.setStyleSheet("background-color: white;")

        layout = QVBoxLayout()

        # 1. 캐릭터 이미지 부분
        self.char_label = QLabel(self)
        self.char_label.setAlignment(Qt.AlignCenter)
        
        # [중요] 실제 캐릭터 이미지가 있다면 아래 주석을 풀고 파일 이름을 적어주세요.
        pixmap = QPixmap("momonga.jpg")
        self.char_label.setPixmap(
            pixmap.scaled(280, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        
        # 이미지가 없을 때를 위한 임시 텍스트 아이콘
        #self.char_label.setText("👩‍🔬") 
        #self.char_label.setFont(QFont("Arial", 60))

        # 2. 안내 대사 (말풍선 느낌)
        self.text_label = QLabel("내놔!", self)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setFont(QFont("Malgun Gothic", 18, QFont.Bold))
        self.text_label.setStyleSheet("color: #333333; padding: 24px;")

        layout.addWidget(self.char_label)
        layout.addWidget(self.text_label)
        self.setLayout(layout)

    # 드래그해서 마우스가 창 안으로 들어올 때
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
            self.text_label.setText("마우스를 놔!")
            self.setStyleSheet("background-color: #e0f7fa;") # 배경색 살짝 변경
        else:
            event.ignore()

    # 창 밖으로 나갈 때 (취소)
    def dragLeaveEvent(self, event):
        self.text_label.setText("내놔")
        self.setStyleSheet("background-color: white;")

    # 드롭했을 때 (마우스를 놓았을 때)
    def dropEvent(self, event):
        self.setStyleSheet("background-color: white;")
        urls = event.mimeData().urls()
        if urls:
            folder_path = urls[0].toLocalFile()
            
            if not os.path.isdir(folder_path):
                self.text_label.setText("앗, 이건 폴더가 아닌 것 같아!\n'폴더'를 드래그 해줘.")
                return

            # UI 화면 갱신 (처리 중 메시지 띄우기)
            self.text_label.setText(f"열심히 처리 중이야...\nOrigin이 켜질 때까지 잠시만 기다려줘!\n\n경로: {folder_path}")
            self.char_label.setText("⏳")
            QApplication.processEvents() 

            try:
                # 여기서 Origin 처리 함수 실행!
                # (pH나 전극 면적 등을 고정값으로 두거나, 필요한 경우 UI 입력칸을 추가할 수 있습니다)
                process_mpt_final(
                    target_folder=folder_path,
                    current_ref="Ag/AgCl (3.5M KCl)",
                    ph_value=7.0,
                    electrode_area=0.5,
                    output_name="LSV_Origin_Modified.opju"
                )
                self.char_label.setText("✨")
                self.text_label.setText("짜잔! 완료됐어!\nOrigin 프로그램을 확인해봐.")
            except Exception as e:
                self.char_label.setText("💦")
                self.text_label.setText(f"에러가 발생했어 ㅠㅠ:\n{e}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = DragDropApp()
    ex.show()
    sys.exit(app.exec_())