"""Núcleo do JFD: áudio, análise musical, transcrição e partituras (sem interface)."""


class Cancelled(Exception):
    """O usuário cancelou o processamento."""


class JFDError(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""
