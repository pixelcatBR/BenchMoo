#!/usr/bin/env python3
"""
BenchMoo 🐮 - benchmark meme

Como funciona:
  1. Cada vaca é o teu ~/cow.py rodando como SUBPROCESS (um processo por vaca).
  2. A cada 2s as vacas DOBRAM (1 -> 2 -> 4 -> 8 ...).
  3. Isso continua até algum componente (CPU, RAM ou GPU) chegar no LIMITE_USO (%).
     Esse é o "nível máximo": o componente que bateu o limite é o gargalo.
  4. No nível máximo ele segura a carga por alguns segundos medindo o FPS.
  5. Resultado:  MooScore = vacas no pico x FPS médio sob carga.

A janela mostra: info do sistema, uso de cada componente (atual/média/pico),
FPS (atual/melhor/média/mínimo/desvio), o cálculo do limite e um histórico.
O overlay no canto da tela mostra o contador de vacas.

Instalar:  pip install PyQt5 psutil
Rodar:     python3 main.py
Sair:      botão "Parar e sair", fechar a janela ou Ctrl+C no terminal.
GPU:       só é lida em placas NVIDIA (usa o nvidia-smi). Sem ele, só CPU e RAM.
"""

import sys
import json
import time
import signal
import shutil
import platform
import statistics
import subprocess
from collections import deque
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QPushButton,
                             QProgressBar, QPlainTextEdit)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap

try:
    import psutil
except ImportError:
    sys.exit("Falta o psutil (ele mede o uso de CPU/RAM). Instale com: pip install psutil")


# ------------------------------ CONFIGURAÇÕES ------------------------------
HOME = Path.home()
COW_PATH = HOME / "cow.py"                 # o teu código da vaca
RECORDE_PATH = HOME / ".benchmoo_best.json"

LIMITE_USO = 90         # % de uso que define o "nível máximo" (de qualquer componente)
MAX_SEGURANCA = 200     # trava de segurança: nunca passa desse número de vacas
INTERVALO_MS = 2000     # tempo entre cada "dobra" de vacas
SEGURAR_MS = 4000       # quanto tempo mede no nível máximo antes de finalizar
SUAVIZAR = 3            # usa a média das últimas N leituras (1 leitura = 0,5s)
# ---------------------------------------------------------------------------


# ------------------------- funções de apoio (sistema) -------------------------
def media(lista):
    return sum(lista) / len(lista) if lista else 0.0


def nvidia_smi(campos):
    """Consulta a placa NVIDIA. Retorna uma lista de textos, ou None se não der."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        saida = subprocess.run(
            ["nvidia-smi", f"--query-gpu={campos}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        if saida.returncode != 0 or not saida.stdout.strip():
            return None
        return [x.strip() for x in saida.stdout.splitlines()[0].split(",")]
    except Exception:
        return None


def ler_gpu():
    """% de uso da GPU (só NVIDIA). None se não tiver."""
    r = nvidia_smi("utilization.gpu")
    try:
        return float(r[0]) if r else None
    except ValueError:
        return None


def nome_cpu():
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for linha in f:
                    if linha.startswith("model name"):
                        return linha.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine()


def info_sistema(tem_gpu):
    nucleos = psutil.cpu_count(logical=False) or "?"
    threads = psutil.cpu_count(logical=True) or "?"
    ram_gb = psutil.virtual_memory().total / 1024 ** 3

    gpu = "não detectada (só NVIDIA via nvidia-smi)"
    if tem_gpu:
        r = nvidia_smi("name")
        if r:
            gpu = r[0]

    return (f"CPU: {nome_cpu()} ({nucleos} núcleos / {threads} threads)\n"
            f"RAM: {ram_gb:.1f} GB\n"
            f"GPU: {gpu}\n"
            f"SO : {platform.system()} {platform.release()}")


# ------------------------------ overlay contador ------------------------------
class ContadorOverlay(QWidget):
    """Overlay no canto da tela mostrando a quantidade de vacas (🐮 x 16)."""

    def __init__(self):
        super().__init__()

        # mesmas flags do teu cow.py: sem borda, sempre por cima, fundo transparente
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # o mouse atravessa

        layout = QHBoxLayout(self)

        # ícone: usa o vaca.png da home, ou o emoji se não achar
        self.icone = QLabel()
        pixmap = QPixmap(str(HOME / "vaca.png"))
        if not pixmap.isNull():
            self.icone.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
        else:
            self.icone.setText("🐮")
            self.icone.setStyleSheet("font-size: 60px; background: transparent;")

        # o número
        self.numero = QLabel("x 0")
        self.numero.setStyleSheet(
            "font-size: 48px; font-weight: bold; color: white;"
            "background: rgba(0, 0, 0, 150); border-radius: 12px; padding: 4px 14px;"
        )

        layout.addWidget(self.icone)
        layout.addWidget(self.numero)
        self.colocar_no_canto()

    def colocar_no_canto(self):
        tela = QApplication.primaryScreen().geometry()
        self.adjustSize()
        self.move(tela.right() - self.width() - 20, 20)  # canto superior direito

    def atualizar(self, quantidade):
        self.numero.setText(f"x {quantidade}")
        self.colocar_no_canto()


# ------------------------------ janela principal ------------------------------
class BenchMoo(QWidget):
    """Janela informativa + controle das vacas (subprocessos)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BenchMoo 🐮")
        self.setMinimumWidth(520)

        # ---------- o que vai ser monitorado ----------
        self.tem_gpu = ler_gpu() is not None
        self.componentes = ["CPU", "RAM"] + (["GPU"] if self.tem_gpu else [])
        self.uso_atual = {nome: 0.0 for nome in self.componentes}
        self.leituras = {nome: deque(maxlen=SUAVIZAR) for nome in self.componentes}
        self.todas = {nome: [] for nome in self.componentes}  # pra média e pico

        # ---------- estado / métricas ----------
        self.processos = []            # subprocessos das vacas
        self.fase = "multiplicando"    # multiplicando -> segurando -> concluido
        self.pico_vacas = 0
        self.amostras = 0
        self.fps_atual = 0.0
        self.fps_todas = []            # todos os FPS medidos
        self.fps_carga = []            # FPS medidos só no nível máximo
        self.score = 0.0
        self.recorde = self.carregar_recorde()
        self.novo_recorde = False

        self.frames = 0
        self.tempo_janela = 0.0
        self.ultimo = time.perf_counter()
        self.t0 = time.perf_counter()

        psutil.cpu_percent(None)       # inicializa a medição de CPU

        self.montar_janela()

        # ---------- overlay do contador ----------
        self.contador = ContadorOverlay()
        self.contador.show()

        # ---------- começa: primeira vaca ----------
        self.lancar_vaca()

        # timer que mede os FPS (~60 vezes por segundo)
        self.timer_fps = QTimer()
        self.timer_fps.setTimerType(Qt.PreciseTimer)
        self.timer_fps.timeout.connect(self.tick)
        self.timer_fps.start(16)

        # timer que multiplica as vacas
        self.timer_mult = QTimer()
        self.timer_mult.timeout.connect(self.multiplicar)
        self.timer_mult.start(INTERVALO_MS)

    # ------------------------------ visual ------------------------------
    def montar_janela(self):
        mono = "font-family: monospace; font-size: 13px;"

        def secao(texto):
            rotulo = QLabel(f"<b>{texto}</b>")
            rotulo.setStyleSheet("margin-top: 8px;")
            return rotulo

        self.titulo = QLabel("🐮 Vacas: 0")
        self.titulo.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.subtitulo = QLabel("")

        sistema = QLabel(info_sistema(self.tem_gpu))
        sistema.setStyleSheet(mono)
        sistema.setWordWrap(True)

        # barras de uso de cada componente
        grade = QGridLayout()
        self.barras = {}
        self.textos = {}
        for linha, nome in enumerate(self.componentes):
            grade.addWidget(QLabel(nome), linha, 0)
            barra = QProgressBar()
            barra.setRange(0, 100)
            grade.addWidget(barra, linha, 1)
            texto = QLabel()
            texto.setStyleSheet(mono)
            grade.addWidget(texto, linha, 2)
            self.barras[nome] = barra
            self.textos[nome] = texto

        self.fps_info = QLabel()
        self.fps_info.setStyleSheet(mono)

        self.calculo = QLabel()
        self.calculo.setStyleSheet(mono)

        self.historico = QPlainTextEdit()
        self.historico.setReadOnly(True)
        self.historico.setMaximumHeight(150)
        self.historico.setStyleSheet(mono)

        botao = QPushButton("Parar e sair")
        botao.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.titulo)
        layout.addWidget(self.subtitulo)
        layout.addWidget(secao("Sistema"))
        layout.addWidget(sistema)
        layout.addWidget(secao(f"Uso dos componentes (limite: {LIMITE_USO}%)"))
        layout.addLayout(grade)
        layout.addWidget(secao("FPS"))
        layout.addWidget(self.fps_info)
        layout.addWidget(secao("Cálculo"))
        layout.addWidget(self.calculo)
        layout.addWidget(secao("Histórico (a cada dobra de vacas)"))
        layout.addWidget(self.historico)
        layout.addWidget(botao)

    def log(self, texto):
        self.historico.appendPlainText(texto)
        print(texto)

    # ------------------------------ vacas ------------------------------
    def lancar_vaca(self):
        """Roda o cow.py como subprocesso (uma vaca = um processo)."""
        try:
            p = subprocess.Popen(
                [sys.executable, str(COW_PATH)],
                cwd=str(HOME),  # o cow.py procura vaca.png e o mp3 aqui
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.processos.append(p)
        except Exception as erro:
            print("Não consegui rodar o cow.py:", erro)

    def vivas(self):
        """Quantas vacas ainda estão rodando."""
        return sum(1 for p in self.processos if p.poll() is None)

    def suavizado(self, nome):
        """Média das últimas leituras de um componente (evita decidir por um pico."""
        return media(self.leituras[nome])

    def gargalo(self):
        """O componente mais carregado agora."""
        nome = max(self.componentes, key=self.suavizado)
        return nome, self.suavizado(nome)

    def multiplicar(self):
        """Dobra as vacas, a não ser que algum componente já tenha batido o limite."""
        if self.fase != "multiplicando":
            return

        n = self.vivas()
        if n == 0:
            self.finalizar("as vacas acabaram antes de bater o limite")
            return

        self.log_nivel(n)

        nome, valor = self.gargalo()
        if valor >= LIMITE_USO:
            self.nivel_maximo(f"{nome} chegou em {valor:.0f}% com {n} vacas")
        elif n >= MAX_SEGURANCA:
            self.nivel_maximo(f"trava de segurança ({MAX_SEGURANCA} vacas)")
        else:
            for _ in range(min(n, MAX_SEGURANCA - n)):  # dobra
                self.lancar_vaca()

    def nivel_maximo(self, motivo):
        self.fase = "segurando"
        self.timer_mult.stop()
        self.log(f"--> NÍVEL MÁXIMO: {motivo}")
        QTimer.singleShot(SEGURAR_MS, lambda: self.finalizar(motivo))

    def log_nivel(self, n):
        t = time.perf_counter() - self.t0
        usos = " | ".join(f"{nome} {self.suavizado(nome):3.0f}%" for nome in self.componentes)
        self.log(f"{t:5.1f}s | {n:3d} vacas | FPS {self.fps_atual:5.1f} | {usos}")

    # ------------------------------ medição ------------------------------
    def tick(self):
        agora = time.perf_counter()
        self.tempo_janela += agora - self.ultimo
        self.ultimo = agora
        self.frames += 1

        # a cada meio segundo calcula o FPS e lê o uso dos componentes
        if self.tempo_janela >= 0.5:
            fps = self.frames / self.tempo_janela
            self.frames = 0
            self.tempo_janela = 0.0
            self.amostrar(fps)

    def amostrar(self, fps):
        n = self.vivas()
        self.amostras += 1
        self.pico_vacas = max(self.pico_vacas, n)

        # --- FPS ---
        self.fps_atual = fps
        self.fps_todas.append(fps)
        if self.fase == "segurando":
            self.fps_carga.append(fps)

        # --- uso dos componentes ---
        self.uso_atual["CPU"] = psutil.cpu_percent(None)
        self.uso_atual["RAM"] = psutil.virtual_memory().percent
        if self.tem_gpu and self.amostras % 4 == 0:  # nvidia-smi é lento: lê a cada 2s
            gpu = ler_gpu()
            if gpu is not None:
                self.uso_atual["GPU"] = gpu
        for nome in self.componentes:
            self.leituras[nome].append(self.uso_atual[nome])
            self.todas[nome].append(self.uso_atual[nome])

        self.atualizar_tela(n)

    # ------------------------------ tela ------------------------------
    def atualizar_tela(self, n):
        self.contador.atualizar(n)
        self.titulo.setText(f"🐮 Vacas: {n}")
        self.subtitulo.setText({
            "multiplicando": "multiplicando até algum componente bater o limite...",
            "segurando": "nível máximo! medindo o FPS sob carga...",
            "concluido": "concluído!",
        }[self.fase])

        # componentes: atual / média / pico
        for nome in self.componentes:
            atual = self.uso_atual[nome]
            lista = self.todas[nome]
            pico = max(lista) if lista else 0
            self.barras[nome].setValue(int(atual))
            self.textos[nome].setText(
                f"atual {atual:3.0f}% | média {media(lista):3.0f}% | pico {pico:3.0f}%")

        # FPS
        lista = self.fps_todas
        melhor = max(lista) if lista else 0
        minimo = min(lista) if lista else 0
        desvio = statistics.pstdev(lista) if len(lista) > 1 else 0
        linhas = [
            f"Atual : {self.fps_atual:6.1f}",
            f"Melhor: {melhor:6.1f}",
            f"Média : {media(lista):6.1f}",
            f"Mínimo: {minimo:6.1f}",
            f"Desvio: {desvio:6.1f}   (quanto menor, mais estável)",
        ]
        if self.fps_carga:
            linhas.append(f"Média sob carga máxima: {media(self.fps_carga):.1f}")
        if self.recorde:
            linhas.append(f"Recorde de MooScore: {self.recorde.get('score', 0):.0f}")
        if self.novo_recorde:
            linhas.append("🎉 NOVO RECORDE!")
        self.fps_info.setText("\n".join(linhas))

        self.calculo.setText(self.texto_calculo())

    def texto_calculo(self):
        linhas = [f"Limite: {LIMITE_USO}% em qualquer componente",
                  f"(usa a média das últimas {SUAVIZAR} leituras de 0,5s)"]
        for nome in self.componentes:
            linhas.append(f"  {nome}: {self.suavizado(nome):.0f}%")

        nome, valor = self.gargalo()
        if valor >= LIMITE_USO:
            veredito = "NÍVEL MÁXIMO"
        else:
            veredito = f"faltam {LIMITE_USO - valor:.0f} pontos pro limite"
        linhas.append(f"Gargalo: {nome} com {valor:.0f}%  ({veredito})")

        if self.fase == "concluido":
            fps_carga = media(self.fps_carga or self.fps_todas)
            linhas.append("")
            linhas.append("MooScore = vacas no pico x FPS médio sob carga")
            linhas.append(f"         = {self.pico_vacas} x {fps_carga:.1f} = {self.score:.0f}")
        return "\n".join(linhas)

    # ------------------------------ final / recorde ------------------------------
    def carregar_recorde(self):
        try:
            return json.loads(RECORDE_PATH.read_text())
        except Exception:
            return {}

    def finalizar(self, motivo):
        if self.fase == "concluido":
            return
        self.fase = "concluido"
        self.timer_fps.stop()
        self.timer_mult.stop()
        self.matar_vacas()

        fps_carga = media(self.fps_carga or self.fps_todas)
        self.score = self.pico_vacas * fps_carga

        nome, valor = self.gargalo()
        if self.score > self.recorde.get("score", 0):
            self.novo_recorde = True
            self.recorde = {"score": self.score, "fps_medio": fps_carga,
                            "vacas": self.pico_vacas, "gargalo": nome}
            try:
                RECORDE_PATH.write_text(json.dumps(self.recorde, indent=2))
            except Exception:
                pass

        self.log("=== RESULTADO ===")
        self.log(f"Motivo do fim : {motivo}")
        self.log(f"Vacas no pico : {self.pico_vacas}")
        self.log(f"FPS sob carga : {fps_carga:.1f}")
        self.log(f"MooScore      : {self.pico_vacas} x {fps_carga:.1f} = {self.score:.0f}")

        self.atualizar_tela(self.vivas())

    # ------------------------------ sair ------------------------------
    def matar_vacas(self):
        for p in self.processos:
            if p.poll() is None:
                p.terminate()

    def closeEvent(self, event):
        self.matar_vacas()
        self.contador.close()
        QApplication.quit()
        event.accept()


def main():
    app = QApplication(sys.argv)

    if not COW_PATH.exists():
        sys.exit(f"Não achei {COW_PATH}. Coloque o cow.py na sua pasta home.")

    janela = BenchMoo()
    janela.show()

    # Ctrl+C no terminal também fecha tudo
    signal.signal(signal.SIGINT, lambda *args: janela.close())
    timer_sinal = QTimer()
    timer_sinal.timeout.connect(lambda: None)
    timer_sinal.start(250)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
