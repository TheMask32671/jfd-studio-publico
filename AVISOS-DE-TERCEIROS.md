# Componentes de terceiros usados pelo JFD

| Componente | Para que serve | Licença |
|---|---|---|
| Python | Linguagem do programa | PSF License |
| NumPy, SciPy | Processamento de sinais e análise | BSD |
| PySide6 (Qt for Python) | Interface | LGPL v3 |
| PyTorch, torchaudio | Execução dos modelos de IA | BSD |
| Demucs / HTDemucs (Meta Research) | Separação de instrumentos | MIT (código) |
| Verovio | Desenho das partituras | LGPL v3 |
| FFmpeg (via imageio-ffmpeg) | Leitura dos formatos de áudio | LGPL/GPL, conforme a compilação |
| sounddevice / PortAudio | Reprodução e gravação | MIT |

Pontos a conferir **antes de vender ou distribuir o JFD comercialmente**:

- **Modelos pré-treinados do Demucs**: o código é MIT, mas vale confirmar com um advogado
  as condições de uso comercial dos pesos pré-treinados.
- **FFmpeg**: o executável embutido é chamado como programa separado. Dependendo da
  compilação (GPL ou LGPL) há obrigações de aviso e de oferta do código-fonte.
- **PySide6 e Verovio (LGPL)**: exigem manter os avisos de licença e permitir a
  substituição das bibliotecas.

Origem dos modelos embutidos: o servidor oficial dos modelos do Demucs estava com o certificado
de segurança vencido na data da montagem. Os arquivos foram obtidos de dois repositórios
independentes no Hugging Face, eram idênticos entre si e conferem com o código de verificação
presente no nome oficial de cada arquivo. O SHA-256 completo está registrado em
`jfd/core/models.py` e é conferido a cada montagem e antes de cada uso.

Este arquivo é informativo e não substitui orientação jurídica.
