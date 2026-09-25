from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton

class DisclosureSection(QWidget):
    """Collapsible content with a movable toggle and stable expanded state."""

    def __init__(self, closed_text, open_text, content, parent=None):
        super().__init__(parent)
        self._closed_text = closed_text
        self._open_text = open_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton(closed_text)
        self.toggle.setCheckable(True)
        self.toggle.clicked.connect(self._sync)
        layout.addWidget(self.toggle)
        self.content = content
        self.content.hide()
        layout.addWidget(self.content, stretch=1)
        layout.addStretch()

    def _sync(self):
        expanded = self.toggle.isChecked()
        self.content.setVisible(expanded)
        self.toggle.setText(self._open_text if expanded else self._closed_text)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)
        self._sync()

