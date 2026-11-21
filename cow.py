import sys
import subprocess
import os
import shutil
import signal
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QPainter, QPixmap

# pasta onde ESTE arquivo está (funciona em Linux e Windows)
BASE_DIR = Path(__file__).resolve().parent
VACA_PNG = BASE_DIR / "vaca.png"
VACA_MP3 = BASE_DIR / "vaca_mugido.mp3"


def tocar_mp3(caminho):
    """Toca um mp3 usando o player disponível no sistema. Retorna o Popen ou None."""
    if not caminho.exists():
        return None

    candidatos = []

    # Linux / macOS
    if shutil.which("mpg123"):
        candidatos.append(["mpg123", "-q", str(caminho)])
    if shutil.which("ffplay"):
        candidatos.append(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(caminho)])
    if shutil.which("cvlc"):
        candidatos.append(["cvlc", "--play-and-exit", "--intf", "dummy", str(caminho)])

    # Windows
    if sys.platform.startswith("win"):
        if shutil.which("ffplay"):
            candidatos.append(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(caminho)])
        # fallback: abre com o player padrão via start (não dá pra matar depois)
        # melhor evitar; então só usamos se tiver ffplay

    for cmd in candidatos:
        try:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            continue

    return None


class VacaOverlay(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(200, 200)

        self.label = QLabel(self)
        self.label.setGeometry(0, 0, 200, 200)
        self.label.setAlignment(Qt.AlignCenter)

        self.carregar_imagem()

        self.screen = QApplication.primaryScreen().geometry()
        self.y_pos = self.screen.height() - 250
        self.x_inicial = -250
        self.move(self.x_inicial, self.y_pos)

        self.animacao = QPropertyAnimation(self, b"pos")
        self.animacao.setDuration(3000)
        self.animacao.setEasingCurve(QEasingCurve.InOutQuad)

        self.ja_mugiu = False
        self.processo_audio = None

        QTimer.singleShot(1000, self.entrar_na_tela)

    def carregar_imagem(self):
        if VACA_PNG.exists():
            pixmap = QPixmap(str(VACA_PNG))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation)
                self.label.setPixmap(pixmap)
                return True

        self.label.setText("🐮")
        self.label.setStyleSheet("font-size: 150px; background: transparent;")
        return False

    def entrar_na_tela(self):
        centro_x = (self.screen.width() - self.width()) // 2

        self.animacao.setStartValue(QPoint(self.x_inicial, self.y_pos))
        self.animacao.setEndValue(QPoint(centro_x, self.y_pos))
        self.animacao.finished.connect(self.mugir)
        self.animacao.start()

    def mugir(self):
        if self.ja_mugiu:
            return

        self.ja_mugiu = True

        try:
            self.processo_audio = tocar_mp3(VACA_MP3)
        except Exception:
            self.processo_audio = None

        QTimer.singleShot(2500, self.sair_da_tela)

    def sair_da_tela(self):
        x_final = self.screen.width() + 200

        self.animacao.setStartValue(self.pos())
        self.animacao.setEndValue(QPoint(x_final, self.y_pos))
        self.animacao.setDuration(2500)
        self.animacao.finished.connect(self.fechar)
        self.animacao.start()

    def fechar(self):
        if self.processo_audio:
            try:
                self.processo_audio.terminate()
                try:
                    self.processo_audio.wait(timeout=0.5)
                except Exception:
                    pass
            except Exception:
                try:
                    self.processo_audio.kill()
                except Exception:
                    pass
            self.processo_audio = None

        self.close()
        QApplication.quit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.end()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    vaca = VacaOverlay()
    vaca.show()
    sys.exit(app.exec_())
