"""Versão do software do gateway (heartbeat da frota). A imagem Docker grava EDGE_VERSION no build."""
import os

__version__ = os.environ.get("EDGE_VERSION") or "0.5.0"
