#!/usr/bin/env python3
"""Processador modular de PDFs digitalizados para o projeto pdf-splitter.

Cada operação é opcional:
  --separar-duplas
  --corrigir-inclinacao
  --recortar-margens
  --uniformizar
  --ocr

O processamento usa arquivos temporários para evitar manter todas as imagens
na RAM. O PDF de entrada nunca é sobrescrito.
"""

import argparse
import gc
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
import pytesseract
from PIL import Image, ImageOps

EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DPI_PADRAO = 250


def corrigir_inclinacao(img):
    """Corrige pequenas inclinações detectando linhas horizontais."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    escala = min(1.0, 1000 / max(h, w))
    pequeno = cv2.resize(gray, None, fx=escala, fy=escala,
                         interpolation=cv2.INTER_AREA)
    binaria = cv2.threshold(
        pequeno, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    linhas = cv2.HoughLinesP(
        binaria, rho=1, theta=np.pi / 1800, threshold=50,
        minLineLength=max(35, int(pequeno.shape[1] * 0.12)),
        maxLineGap=12,
    )
    if linhas is None:
        return img

    angulos = []
    for x1, y1, x2, y2 in linhas.reshape(-1, 4):
        angulo = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if 0.15 < abs(angulo) <= 5:
            comprimento = np.hypot(x2 - x1, y2 - y1)
            angulos.append((angulo, comprimento))
    if not angulos:
        return img

    # Mediana ponderada pelo comprimento das linhas.
    angulos.sort(key=lambda item: item[0])
    peso_total = sum(peso for _, peso in angulos)
    acumulado = 0
    angulo_final = 0.0
    for valor, peso in angulos:
        acumulado += peso
        if acumulado >= peso_total / 2:
            angulo_final = valor
            break

    matriz = cv2.getRotationMatrix2D((w / 2, h / 2), angulo_final, 1.0)
    return cv2.warpAffine(
        img, matriz, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
    )


def detectar_dobra(img, corte=None):
    """Detecta a dobra na faixa central; corte manual é uma porcentagem."""
    h, w = img.shape[:2]
    if corte is not None:
        return int(w * corte / 100)

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    inicio, fim = int(w * 0.43), int(w * 0.57)
    faixa = gray[:, inicio:fim]
    tinta = (faixa < 180).mean(axis=0).astype(np.float32)
    tinta = cv2.GaussianBlur(tinta.reshape(1, -1), (0, 0), sigmaX=3).ravel()
    return inicio + int(np.argmin(tinta))


def dividir_imagem(img, corte=None):
    """Divide uma imagem de página dupla em metade esquerda e direita."""
    dobra = detectar_dobra(img, corte)
    if not img.shape[1] * 0.35 <= dobra <= img.shape[1] * 0.65:
        raise ValueError("O corte detectado ficou fora da região central.")
    return img[:, :dobra], img[:, dobra:]


def detectar_limites_conteudo(img):
    """Retorna (x, y, largura, altura) ou None se o recorte for incerto."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    escala = min(1.0, 1200 / max(h, w))
    pequeno = cv2.resize(gray, None, fx=escala, fy=escala,
                         interpolation=cv2.INTER_AREA)
    ph, pw = pequeno.shape
    tinta = pequeno < 190
    ys = np.flatnonzero(tinta.sum(axis=1) >= max(2, int(pw * 0.001)))
    xs = np.flatnonzero(tinta.sum(axis=0) >= max(2, int(ph * 0.001)))

    if len(xs) == 0 or len(ys) == 0 or int(tinta.sum()) < 80:
        return None

    x1, x2 = int(xs[0]), int(xs[-1])
    y1, y2 = int(ys[0]), int(ys[-1])
    largura, altura = x2 - x1 + 1, y2 - y1 + 1
    if largura < pw * 0.12 or altura < ph * 0.12:
        return None
    if largura > pw * 0.995 and altura > ph * 0.995:
        return None

    inversa = 1 / escala
    x = max(0, int(np.floor(x1 * inversa)))
    y = max(0, int(np.floor(y1 * inversa)))
    x_fim = min(w, int(np.ceil((x2 + 1) * inversa)))
    y_fim = min(h, int(np.ceil((y2 + 1) * inversa)))
    if x_fim <= x or y_fim <= y:
        return None
    return x, y, x_fim - x, y_fim - y


def recortar_margens(img, margem_percentual=4.0):
    """Recorta margens vazias com uma margem de segurança."""
    limites = detectar_limites_conteudo(img)
    if limites is None:
        return img, False

    x, y, largura, altura = limites
    h, w = img.shape[:2]
    margem_px = int(max(largura, altura) * margem_percentual / 100)
    x1, y1 = max(0, x - margem_px), max(0, y - margem_px)
    x2, y2 = min(w, x + largura + margem_px), min(h, y + altura + margem_px)
    recorte = img[y1:y2, x1:x2]
    if recorte.size == 0:
        return img, False
    return recorte, True


def aplicar_operacoes(img, opcoes, corte, margem):
    """Aplica somente as operações escolhidas à imagem atual."""
    if opcoes["corrigir_inclinacao"]:
        img = corrigir_inclinacao(img)

    confiavel = True
    if opcoes["recortar_margens"]:
        img, confiavel = recortar_margens(img, margem)

    if opcoes["separar_duplas"] and img.shape[1] > img.shape[0]:
        return [(parte, confiavel) for parte in dividir_imagem(img, corte)]
    return [(img, confiavel)]


def salvar_temporaria(img, confiavel, pasta, indice, dpi):
    caminho = Path(pasta) / f"pagina_{indice:05d}.png"
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok = cv2.imwrite(str(caminho), bgr)
    del bgr
    if not ok:
        raise OSError(f"Falha ao gravar imagem temporária: {caminho}")
    h, w = img.shape[:2]
    return caminho, confiavel, w, h, dpi


def preparar_pdf(src, opcoes, corte, margem, pasta_temp, dpi):
    """Renderiza e salva cada página processada em disco, uma por vez."""
    doc = fitz.open(src)
    metadados = []
    indice_saida = 0
    try:
        total = len(doc)
        for numero, pagina in enumerate(doc, start=1):
            pix = pagina.get_pixmap(
                matrix=fitz.Matrix(dpi / 72, dpi / 72),
                colorspace=fitz.csRGB, alpha=False,
            )
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            ).copy()
            del pix

            if opcoes["separar_duplas"] and pagina.rect.width > pagina.rect.height:
                partes = dividir_imagem(img, corte)
            else:
                partes = (img,)
            del img

            for parte in partes:
                processada = parte
                confiavel = True
                if opcoes["corrigir_inclinacao"]:
                    processada = corrigir_inclinacao(processada)
                if opcoes["recortar_margens"]:
                    processada, confiavel = recortar_margens(processada, margem)
                metadados.append(
                    salvar_temporaria(processada, confiavel, pasta_temp, indice_saida, dpi)
                )
                indice_saida += 1
                del parte, processada
            del partes
            gc.collect()
            print(f"Preparação: página {numero}/{total}", flush=True)
    finally:
        doc.close()
    return metadados


def preparar_imagem(src, opcoes, corte, margem, pasta_temp, dpi):
    """Processa imagens estáticas ou multipágina, um quadro por vez."""
    metadados = []
    with Image.open(src) as origem:
        total = getattr(origem, "n_frames", 1)
        indice_saida = 0
        for numero in range(total):
            origem.seek(numero)
            frame = ImageOps.exif_transpose(origem.copy()).convert("RGB")
            img = np.asarray(frame).copy()
            del frame

            if opcoes["separar_duplas"] and img.shape[1] > img.shape[0]:
                partes = dividir_imagem(img, corte)
            else:
                partes = (img,)
            del img

            for parte in partes:
                processada = parte
                confiavel = True
                if opcoes["corrigir_inclinacao"]:
                    processada = corrigir_inclinacao(processada)
                if opcoes["recortar_margens"]:
                    processada, confiavel = recortar_margens(processada, margem)
                metadados.append(
                    salvar_temporaria(processada, confiavel, pasta_temp, indice_saida, dpi)
                )
                indice_saida += 1
                del parte, processada
            del partes
            gc.collect()
            print(f"Preparação: quadro {numero + 1}/{total}", flush=True)
    return metadados


def criar_pagina_uniforme(img, largura, altura):
    """Centraliza uma imagem numa tela branca de dimensões fixas."""
    h, w = img.shape[:2]
    tela = np.full((altura, largura, 3), 255, dtype=np.uint8)
    x, y = (largura - w) // 2, (altura - h) // 2
    tela[y:y + h, x:x + w] = img
    return tela


def adicionar_imagem_pdf(img, saida, dpi):
    """Adiciona a imagem como página PDF sem OCR."""
    h, w = img.shape[:2]
    pagina = saida.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
    ok, buffer = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    if not ok:
        raise OSError("Não foi possível codificar a página.")
    pagina.insert_image(pagina.rect, stream=buffer.tobytes())


def adicionar_pagina_ocr(img, saida, idioma, dpi):
    """Gera uma página PDF com camada de texto OCR."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    pil = Image.fromarray(gray)
    dados = pytesseract.image_to_pdf_or_hocr(
        pil, extension="pdf", lang=idioma, config=f"--psm 3 --dpi {dpi}"
    )
    pdf_ocr = fitz.open(stream=dados, filetype="pdf")
    try:
        saida.insert_pdf(pdf_ocr)
    finally:
        pdf_ocr.close()


def processar(entrada, destino, opcoes, dpi=250, idioma="por",
              corte=None, margem=4.0, progresso=print):
    """Processa um PDF/imagem com operações explicitamente selecionadas."""
    src = Path(entrada).expanduser().resolve()
    dst = Path(destino).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {src}")
    if src.suffix.lower() != ".pdf" and src.suffix.lower() not in EXTENSOES_IMAGEM:
        raise ValueError("Formato não suportado. Use PDF ou imagem.")
    if src == dst:
        raise ValueError("A saída não pode sobrescrever o arquivo original.")
    if not any(opcoes.values()):
        raise ValueError("Selecione pelo menos uma operação.")
    if not 150 <= dpi <= 400:
        raise ValueError("DPI deve ficar entre 150 e 400.")
    if corte is not None and not 40 <= corte <= 60:
        raise ValueError("A posição de corte deve ficar entre 40% e 60%.")
    if not 1 <= margem <= 15:
        raise ValueError("A margem de segurança deve ficar entre 1% e 15%.")

    if opcoes["ocr"]:
        idiomas = pytesseract.get_languages(config="")
        if idioma not in idiomas:
            raise ValueError(f"Idioma Tesseract '{idioma}' indisponível.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdf-splitter-") as pasta_temp:
        if src.suffix.lower() == ".pdf":
            paginas = preparar_pdf(src, opcoes, corte, margem, pasta_temp, dpi)
        else:
            paginas = preparar_imagem(src, opcoes, corte, margem, pasta_temp, dpi)

        if not paginas:
            raise ValueError("Nenhuma página foi gerada.")

        if opcoes["uniformizar"]:
            largura_max = max(item[2] for item in paginas)
            altura_max = max(item[3] for item in paginas)
        else:
            largura_max = altura_max = None

        saida = fitz.open()
        try:
            total = len(paginas)
            preservadas = sum(not item[1] for item in paginas)
            for indice, (caminho, confiavel, _, _, dpi_pagina) in enumerate(paginas, start=1):
                bgr = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
                if bgr is None:
                    raise OSError(f"Falha ao ler página temporária: {caminho}")
                img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                del bgr

                if opcoes["uniformizar"]:
                    uniforme = criar_pagina_uniforme(img, largura_max, altura_max)
                    del img
                    img = uniforme

                if opcoes["ocr"]:
                    adicionar_pagina_ocr(img, saida, idioma, dpi_pagina)
                else:
                    adicionar_imagem_pdf(img, saida, dpi_pagina)

                del img
                gc.collect()
                msg = f"Finalização: página {indice}/{total}"
                if not confiavel:
                    msg += " (margens mantidas: detecção incerta)"
                progresso(msg)

            saida.set_metadata({
                "title": dst.stem,
                "producer": "pdf-splitter",
            })
            saida.save(dst, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
        finally:
            saida.close()

    return {
        "arquivo": str(dst),
        "paginas": len(paginas),
        "paginas_com_margens_preservadas": preservadas,
        "tamanho_bytes": dst.stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Processador modular de PDFs digitalizados."
    )
    parser.add_argument("arquivo", help="PDF ou imagem de entrada")
    parser.add_argument("-o", "--saida", required=True, help="PDF de saída")
    parser.add_argument("--separar-duplas", action="store_true")
    parser.add_argument("--corrigir-inclinacao", action="store_true")
    parser.add_argument("--recortar-margens", action="store_true")
    parser.add_argument("--uniformizar", action="store_true")
    parser.add_argument("--ocr", action="store_true", help="Adicionar texto pesquisável")
    parser.add_argument("--dpi", type=int, default=DPI_PADRAO)
    parser.add_argument("--idioma", default="por")
    parser.add_argument("--corte", type=float, help="Corte manual em porcentagem (40–60)")
    parser.add_argument("--margem", type=float, default=4.0,
                        help="Margem de segurança em porcentagem (1–15)")
    args = parser.parse_args()

    opcoes = {
        "separar_duplas": args.separar_duplas,
        "corrigir_inclinacao": args.corrigir_inclinacao,
        "recortar_margens": args.recortar_margens,
        "uniformizar": args.uniformizar,
        "ocr": args.ocr,
    }
    try:
        resultado = processar(
            args.arquivo, args.saida, opcoes,
            dpi=args.dpi, idioma=args.idioma,
            corte=args.corte, margem=args.margem,
        )
        print(f"PDF pronto: {resultado['arquivo']}")
        print(f"Páginas geradas: {resultado['paginas']}")
        print(f"Páginas com margens preservadas por incerteza: "
              f"{resultado['paginas_com_margens_preservadas']}")
        print(f"Tamanho: {resultado['tamanho_bytes'] / 1024 / 1024:.1f} MB")
    except (OSError, ValueError, RuntimeError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
