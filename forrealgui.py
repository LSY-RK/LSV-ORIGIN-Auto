import sys
import os
import glob
import re
from collections import defaultdict

import pandas as pd
import originpro as op
import OriginExt as oe

import PyQt5

qt_plugin_dir = os.path.join(
    os.path.dirname(PyQt5.__file__), "Qt5", "plugins"
)
os.environ["QT_PLUGIN_PATH"] = qt_plugin_dir
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(
    qt_plugin_dir, "platforms"
)

# PyQt5 모듈들 
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                             QFormLayout, QComboBox, QDoubleSpinBox, 
                             QFileDialog, QGroupBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap


REF_DICT = {
    "Hg/HgO": 0.098,
    "Ag/AgCl (3.5M KCl)": 0.205,
}

def resource_path(relative_path):
    base_path = getattr(
        sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
    )
    return os.path.join(base_path, relative_path)

def connect_origin():
    import originpro.config as opc
    try:
        op.attach()
    except Exception as attach_error:
        message = str(attach_error).lower()
        is_rpc_error = "rpc" in message or "원격 프로시저" in message
        if is_rpc_error and opc.oext and "_app" in vars(opc.po):
            opc.po._app = None
        try:
            op.set_show(False)
        except Exception as start_error:
            raise RuntimeError("Origin에 연결할 수 없습니다. Origin을 실행한 뒤 다시 시도하세요.") from start_error
    print("Origin 연결 완료")

def force_xy_designations(wks, pattern):
    ncols = wks.cols
    if ncols <= 0 or ncols % len(pattern) != 0:
        raise RuntimeError(f"열 수와 패턴 길이가 맞지 않습니다: 열={ncols}, 패턴={pattern}")
    expected = pattern * (ncols // len(pattern))
    codes = {"X": oe.COLTYPE_X, "Y": oe.COLTYPE_Y}
    for index, axis in enumerate(expected):
        wks.obj.Columns(index).SetType(codes[axis])
    print(f"  ✅ {wks.lt_range()}: {expected} 지정 및 검증 완료")

def create_sheet(df, sheet_name, pattern):
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
    with open(file, encoding="latin1") as mpt_file:
        for line in mpt_file:
            match = re.match(r"^\s*Nb header lines\s*:\s*(\d+)", line)
            if match:
                header_lines = int(match.group(1))
                break
        else:
            raise ValueError(f"MPT 파일에서 헤더 줄 수를 찾을 수 없습니다: {file}")
    return pd.read_csv(file, sep=r"\s+", skiprows=header_lines, header=None, encoding="latin1")

def read_nyquist(file, original_fname):
    raw = read_mpt_table(file)
    if raw.shape[1] < 3:
        raise ValueError(f"Nyquist 데이터에 최소 3열이 필요합니다. 현재: {raw.shape[1]}열")
    real_z = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    minus_imag_z = pd.to_numeric(raw.iloc[:, 2], errors="coerce")
    valid = real_z.notna() & minus_imag_z.notna()
    real_z = real_z[valid].reset_index(drop=True)
    minus_imag_z = minus_imag_z[valid].reset_index(drop=True)
    if real_z.empty:
        raise ValueError("유효한 Real Z / -Imaginary Z 데이터가 없습니다.")
    df = pd.concat([real_z, minus_imag_z], axis=1)
    df.columns = pd.MultiIndex.from_tuples([("Real Z", "Ω", original_fname), ("-Imaginary Z", "Ω", original_fname)])
    return df

def extract_rs(df_nyquist):
    min_position = int(df_nyquist.iloc[:, 1].abs().to_numpy().argmin())
    return float(df_nyquist.iloc[min_position, 0])

def read_lsv(file, original_fname, rs, rhe_shift, electrode_area, current_ref):
    raw = read_mpt_table(file)
    if raw.shape[1] < 8:
        raise ValueError(f"LSV 데이터에 최소 8열이 필요합니다. 현재: {raw.shape[1]}열")
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
    
    potential_rhe = potential + rhe_shift
    current_density = current / electrode_area
    current_amp = current * 0.001
    potential_comp_rhe = potential - current_amp * rs + rhe_shift
    rs_column = pd.Series([rs] + [float("nan")] * (len(potential) - 1))
    
    df = pd.concat([potential, potential_rhe, current, current_density, rs_column, potential_comp_rhe, current_density], axis=1)
    df.columns = pd.MultiIndex.from_tuples([
        (f"Potential vs. {current_ref}", "V", original_fname),
        ("Potential vs. RHE", "V vs.RHE", original_fname),
        ("Current", "mA", original_fname),
        ("Current Density", "mA cm⁻²", original_fname),
        ("Series Resistance (Rs)", "Ω", f"{original_fname} Rs"),
        ("Potential vs. RHE (iR-free)", "V vs.RHE", f"{original_fname} (Comp)"),
        ("Current Density", "mA cm⁻²", f"{original_fname} (Comp)"),
    ])
    return df

def process_mpt_final(target_folder, current_ref, ph_value, electrode_area, save_path):
    if not os.path.isdir(target_folder):
        raise FileNotFoundError(f"폴더가 없습니다: {target_folder}")
    if electrode_area <= 0:
        raise ValueError("전극 면적은 0보다 커야 합니다.")

    rhe_shift = REF_DICT[current_ref] + 0.059 * ph_value

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
                    df_scan = read_lsv(file, fname, rs, rhe_shift, electrode_area, current_ref)
                    scans.append(df_scan)
                except Exception as e:
                    failed_files.append(fname)

            if not scans:
                continue

            create_sheet(pd.concat(scans, axis=1), sheet_name, pattern="XXYYYXY")
            created_sheets += 1

        for base_name, fname, df_nyquist in nyquist_data:
            sheet_name = f"Nyquist_{base_name}"
            create_sheet(df_nyquist, sheet_name, pattern="XY")
            created_sheets += 1

        if created_sheets == 0:
            print("\n저장할 데이터가 없습니다.")
            return

        op.save(save_path)
        print(f"\n✅ {created_sheets}개 시트 저장 완료!\n파일: {save_path}")
    finally:
        try:
            op.exit()
        except Exception:
            pass


class DragDropApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setAcceptDrops(True)
        self.setWindowTitle("LSV Origin 셔틀 - 모몽가 에디션")
        self.resize(860, 560)
        self.setStyleSheet("background-color: white;")

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(22)

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.char_label = QLabel(self)
        self.char_label.setAlignment(Qt.AlignCenter)
        self.char_label.setMinimumSize(300, 360)
        image_path = resource_path("momonga.jpg")
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.char_label.setText(f"이미지를 불러올 수 없습니다.\n{image_path}")
            self.char_label.setWordWrap(True)
            print(f"이미지를 불러올 수 없습니다: {image_path}")
        else:
            self.char_label.setPixmap(
                pixmap.scaled(
                    360, 390, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

        self.text_label = QLabel("앙? 옆에 값 똑바로 맞추고 폴더나 던져!\n확 물어버리기 전에!", self)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setFont(QFont("Malgun Gothic", 13, QFont.Bold))
        self.text_label.setStyleSheet("color: #333333; padding: 8px;")
        left_layout.addWidget(self.char_label, 1)
        left_layout.addWidget(self.text_label)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 15, 0) # 양옆 여백 좀 줬어! 

        param_group = QGroupBox("🧪 니 맘대로 바꾸는 실험 변수")
        param_group.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        
        # 🚨 글씨가 선 파먹는 거 꼴보기 싫어서 윗 공간(margin-top, padding-top) 팍팍 늘렸어!
        param_group.setStyleSheet("""
            QGroupBox { 
                border: 2px solid #ffb6c1; 
                border-radius: 8px; 
                margin-top: 25px; 
                padding-top: 20px;
                padding-bottom: 15px;
            } 
            QGroupBox::title { 
                subcontrol-origin: margin; 
                subcontrol-position: top left;
                left: 15px; 
                padding: 0 8px; 
                color: #ff69b4; 
            }
        """)

        form_layout = QFormLayout()
        form_layout.setSpacing(15) # 입력칸 사이사이도 숨통 트이게 벌려줌!

        self.ref_combo = QComboBox()
        self.ref_combo.addItems(["Ag/AgCl (3.5M KCl)", "Hg/HgO"])
        self.ref_combo.setFont(QFont("Arial", 11))
        self.ref_combo.setMinimumHeight(30) # 누르기 편하게 뚱뚱하게!

        self.ph_spin = QDoubleSpinBox()
        self.ph_spin.setRange(0.0, 14.0)
        self.ph_spin.setValue(7.0)
        self.ph_spin.setSingleStep(0.1)
        self.ph_spin.setFont(QFont("Arial", 11))
        self.ph_spin.setMinimumHeight(30)

        self.area_spin = QDoubleSpinBox()
        self.area_spin.setRange(0.001, 1000.0)
        self.area_spin.setValue(0.5)
        self.area_spin.setDecimals(3)
        self.area_spin.setSingleStep(0.01)
        self.area_spin.setFont(QFont("Arial", 11))
        self.area_spin.setMinimumHeight(30)

        form_layout.addRow("📌 기준 전극 (Ref):", self.ref_combo)
        form_layout.addRow("💧 전해질 pH:", self.ph_spin)
        form_layout.addRow("📏 전극 면적 (cm²):", self.area_spin)
        param_group.setLayout(form_layout)

        drop_hint = QLabel("설정을 확인한 뒤, 처리할 폴더를 이 창에 끌어다 놓으세요.")
        drop_hint.setWordWrap(True)
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setStyleSheet("color: #777777; padding: 20px;")
        
        # 🚨 [가장 중요한 부분] 위아래로 투명 스프링(Stretch)을 넣어서 놈들을 예쁘게 중앙으로 밀어버렸어!
        right_layout.addStretch(1)   # 위에서 밑으로 쫘악 밀어냄!
        right_layout.addWidget(param_group)
        right_layout.addWidget(drop_hint)
        right_layout.addStretch(1)   # 밑에서 위로 밀어내서 완벽한 센터를 맞춤!

        main_layout.addWidget(left_panel, 3)
        main_layout.addWidget(right_panel, 2)
        self.setLayout(main_layout)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
            self.text_label.setText("그래, 그거! 당장 거기서 마우스 떼라고!!")
            self.setStyleSheet("background-color: #ffe4e1;") 
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.text_label.setText("아앙?! 감히 줬다 뺐어?! 당장 안 가져와?!")
        self.setStyleSheet("background-color: white;")

    def dropEvent(self, event):
        self.setStyleSheet("background-color: white;")
        urls = event.mimeData().urls()
        if not urls: return
        
        folder_path = urls[0].toLocalFile()
        if not os.path.isdir(folder_path):
            self.text_label.setText("장난해?! 이건 폴더가 아니잖아!\n눈은 장식이야?! 똑바로 안 가져와?!")
            return

        selected_ref = self.ref_combo.currentText()
        selected_ph = self.ph_spin.value()
        selected_area = self.area_spin.value()

        save_path, _ = QFileDialog.getSaveFileName(
            self, 
            "어디다 저장할지 똑바로 골라!", 
            os.path.join(folder_path, "LSV_Origin_Modified.opju"), 
            "Origin Project (*.opju)"
        )

        if not save_path:
            self.text_label.setText("아앙?! 왜 저장을 취소해! 장난치는 거야?!\n처음부터 다시 던져!!")
            return

        self.text_label.setText("흥! 열심히 일하는 중이니까 얌전히 기다려!!\n(Origin이 백그라운드에서 켜집니다)")
        QApplication.processEvents() 

        try:
            process_mpt_final(target_folder=folder_path, current_ref=selected_ref, ph_value=selected_ph, electrode_area=selected_area, save_path=save_path)
            self.text_label.setText(f"흥! 다 끝났으니까 알아서 확인해!\n이 몸의 은혜에 감사하라고!\n저장됨: {os.path.basename(save_path)}")
        except Exception as e:
            self.text_label.setText(f"아악! 네가 이상한 걸 줘서 에러 났잖아!!\n\n{e}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = DragDropApp()
    ex.show()
    sys.exit(app.exec_())