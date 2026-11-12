# JFD – Intelligent Audio Studio

Estúdio musical inteligente para Windows: melhora a qualidade do áudio, separa os
instrumentos, descobre andamento, tonalidade e acordes, e transforma melodias em
partitura (PDF e MusicXML) e MIDI — com um clique e sem enviar nada para a internet.

Versão atual: **0.1.0 (primeira versão funcional)**.

## O que esta versão faz

| Função | Situação |
|---|---|
| Importar MP3, WAV, FLAC, M4A, AAC, OGG, AIFF, MP4 (arrastar e soltar) | ✅ |
| Gravar pelo microfone ou interface de áudio (pausar, continuar, salvar) | ✅ |
| Análise do arquivo e dos problemas de som (ruído, zumbido, volume, saturação…) | ✅ |
| Remasterização automática nos modos Natural, Profissional e Estúdio, com ANTES/DEPOIS | ✅ |
| Separação: voz, bateria, baixo, guitarra/violão, piano e "outros instrumentos" | ✅ (Demucs) |
| Mesa de mixagem: ouvir só um instrumento, silenciar, volume, panorâmica, baixar | ✅ |
| Andamento (BPM), compasso provável, tonalidade e acordes por compasso | ✅ |
| Transcrição de melodias (uma nota por vez) para MIDI e partitura | ✅ |
| Partituras para instrumentos em Dó, Si♭, Mi♭ e Fá, com transposição correta | ✅ |
| "Analisar e fazer tudo automaticamente", com retomada de projetos interrompidos | ✅ |
| BAIXAR TUDO (ZIP com áudios, partituras, MIDI e relatório) | ✅ |
| Instalador `JFD_Setup.exe` montado automaticamente pelo GitHub | ✅ |

## O que ainda não faz (próximas fases)

- **Isolar individualmente saxofone, trombone, trompete, clarinete etc.** Os modelos abertos
  disponíveis juntam esses instrumentos na faixa "Outros instrumentos". O JFD mostra isso com
  clareza e informa a confiança de cada faixa em vez de inventar separações.
- Transcrição polifônica (acordes de piano e violão) e bateria em MIDI.
- Editor de partitura dentro do programa (por enquanto, o MusicXML abre no MuseScore).
- Mudar a velocidade sem alterar o tom, mudar o tom e repetir trechos na reprodução.
- Aceleração por placa de vídeo (esta versão usa o processador).
- Reparo de saturação (clipping) e atualizações automáticas.

A transcrição e a separação são automáticas e **podem conter erros**; confira as partituras.

## Como obter o instalador

1. Cada envio de código para o ramo `main` dispara a montagem em **Actions**.
2. Em cerca de 10 minutos o arquivo aparece em **Releases** como `JFD_Setup.exe` (cerca de 330 MB).
   As telas e a partitura geradas pelo autoteste ficam no ramo `autoteste`.
3. No computador com Windows 10/11 de 64 bits: baixar, abrir, aceitar os termos e instalar.

Os modelos de IA já vêm dentro do instalador: depois de instalado, o JFD funciona sem internet.
Recomendado: 8 GB de memória ou mais. Sem placa de vídeo dedicada, a separação de uma música
de 4 minutos leva alguns minutos.

## Para desenvolvedores

```
pip install -r requirements.txt
python run_jfd.py                     # abre o programa
python -m pytest tests                # testes do núcleo
python run_jfd.py --selftest out.txt  # autoteste sem janela
pyinstaller jfd.spec                  # gera dist/JFD
```

Estrutura:

- `jfd/core` — áudio, análise, remasterização, separação, transcrição, teoria musical,
  MusicXML, MIDI, projetos e pipeline (sem interface).
- `jfd/ui` — telas em PySide6, reprodução/gravação e tarefas em segundo plano.
- `installer/jfd.iss` — instalador Inno Setup. `jfd.spec` — empacotamento PyInstaller.
- `.github/workflows/build.yml` — testa, empacota, autotesta e publica o instalador.

Licenças dos componentes: veja `AVISOS-DE-TERCEIROS.md`.
