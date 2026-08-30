"""Reusable operator-first dialogs for B300 ST-Link Tools."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QHBoxLayout,QLabel,QPushButton,QTextBrowser,QVBoxLayout,QWidget

class TechnicalDetailsDialog(QDialog):
    """Non-destructive details window with an embeddable body."""
    def __init__(self,title:str,heading:str,description:str="",parent:Optional[QWidget]=None,*,minimum_size:tuple[int,int]=(640,440))->None:
        super().__init__(parent)
        self.setObjectName("technicalDetailsDialog"); self.setWindowTitle(title); self.setMinimumSize(*minimum_size); self.setModal(False)
        root=QVBoxLayout(self); root.setContentsMargins(16,14,16,14); root.setSpacing(10)
        title_label=QLabel(heading); title_label.setObjectName("detailsDialogTitle"); title_label.setStyleSheet("font-size:16px;font-weight:700;color:#0F172A;"); root.addWidget(title_label)
        self.description_label=QLabel(description); self.description_label.setObjectName("detailsDialogDescription"); self.description_label.setWordWrap(True); self.description_label.setStyleSheet("font-size:12px;color:#64748B;"); self.description_label.setVisible(bool(description)); root.addWidget(self.description_label)
        self.body=QWidget(self); self.body_layout=QVBoxLayout(self.body); self.body_layout.setContentsMargins(0,0,0,0); self.body_layout.setSpacing(10); root.addWidget(self.body,1)
        actions=QHBoxLayout(); actions.addStretch(1); close_button=QPushButton("Đóng"); close_button.setObjectName("detailsDialogCloseButton"); close_button.clicked.connect(self.close); actions.addWidget(close_button); root.addLayout(actions)
    def open_window(self)->None:
        self.show(); self.raise_(); self.activateWindow()

class SafetyActionDialog(QDialog):
    """Focused warning/confirmation dialog with optional hidden details."""
    def __init__(self,title:str,heading:str,message:str,*,details:str="",confirm_text:str="Tiếp tục",cancel_text:str="Hủy",severity:str="warning",parent:Optional[QWidget]=None)->None:
        super().__init__(parent)
        self.setObjectName("safetyActionDialog"); self.setWindowTitle(title); self.setModal(True); self.setMinimumWidth(520)
        key=severity if severity in {"warning","danger","info"} else "warning"
        icon,bg,border,fg={"warning":("⚠","#FFF7ED","#FED7AA","#9A3412"),"danger":("!","#FEF2F2","#FECACA","#991B1B"),"info":("i","#EFF6FF","#BFDBFE","#1D4ED8")}[key]
        root=QVBoxLayout(self); root.setContentsMargins(18,16,18,16); root.setSpacing(12)
        header=QHBoxLayout(); badge=QLabel(icon); badge.setAlignment(Qt.AlignmentFlag.AlignCenter); badge.setFixedSize(42,42); badge.setStyleSheet("font-size:18px;font-weight:800;background:%s;color:%s;border:1px solid %s;border-radius:10px;"%(bg,fg,border)); header.addWidget(badge)
        stack=QVBoxLayout(); stack.setSpacing(3); h=QLabel(heading); h.setObjectName("safetyDialogHeading"); h.setWordWrap(True); h.setStyleSheet("font-size:15px;font-weight:700;color:#0F172A;"); stack.addWidget(h); m=QLabel(message); m.setObjectName("safetyDialogMessage"); m.setWordWrap(True); m.setStyleSheet("font-size:12px;color:#475569;"); stack.addWidget(m); header.addLayout(stack,1); root.addLayout(header)
        self.details_button=QPushButton("Xem chi tiết"); self.details_button.setObjectName("safetyDetailsButton"); self.details_button.setVisible(bool(details)); root.addWidget(self.details_button,0,Qt.AlignmentFlag.AlignLeft)
        self.details_view=QTextBrowser(); self.details_view.setObjectName("safetyDetailsView"); self.details_view.setPlainText(details); self.details_view.setMinimumHeight(140); self.details_view.setVisible(False); root.addWidget(self.details_view); self.details_button.clicked.connect(self._toggle_details)
        actions=QHBoxLayout(); actions.addStretch(1); self.cancel_button=QPushButton(cancel_text); self.cancel_button.setObjectName("safetyCancelButton"); self.cancel_button.clicked.connect(self.reject); actions.addWidget(self.cancel_button); self.confirm_button=QPushButton(confirm_text); self.confirm_button.setObjectName("safetyConfirmButton"); self.confirm_button.setDefault(True)
        if key=="danger": self.confirm_button.setStyleSheet("QPushButton{background:#B91C1C;color:white;border:none;border-radius:6px;padding:7px 16px;font-weight:700;}QPushButton:hover{background:#991B1B;}")
        else: self.confirm_button.setStyleSheet("QPushButton{background:#0284C7;color:white;border:none;border-radius:6px;padding:7px 16px;font-weight:700;}QPushButton:hover{background:#0369A1;}")
        self.confirm_button.clicked.connect(self.accept); actions.addWidget(self.confirm_button); root.addLayout(actions)
    def _toggle_details(self)->None:
        visible=not self.details_view.isVisible(); self.details_view.setVisible(visible); self.details_button.setText("Ẩn chi tiết" if visible else "Xem chi tiết"); self.adjustSize()
    @classmethod
    def confirm(cls,parent:QWidget,title:str,heading:str,message:str,*,details:str="",confirm_text:str="Tiếp tục",severity:str="warning")->bool:
        d=cls(title,heading,message,details=details,confirm_text=confirm_text,severity=severity,parent=parent); return d.exec()==QDialog.DialogCode.Accepted
