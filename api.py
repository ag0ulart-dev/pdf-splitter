
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pdf_processor import processar

app = FastAPI(title="PDF Splitter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

JOBS = {}
LOCK = threading.Lock()
PROCESSOR_LOCK = threading.Lock()

MAX_UPLOAD = 100 * 1024 * 1024
BASE_DIR = Path(__file__).resolve().parent
RESULTADOS_DIR = BASE_DIR / "resultados"
RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)

EXTENSOES = {
    ".pdf", ".png", ".jpg", ".jpeg",
    ".tif", ".tiff", ".bmp", ".webp",
}


def atualizar_job(job_id, **dados):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(dados)


def executar_job(job_id):
    with PROCESSOR_LOCK:
        with LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            dados = job.copy()

        atualizar_job(
            job_id,
            status="processing",
            mensagem="Iniciando processamento.",
        )

        try:
            resultado = processar(
                dados["entrada"],
                dados["saida"],
                dados["opcoes"],
                dpi=dados["dpi"],
                idioma=dados["idioma"],
                corte=dados["corte"],
                margem=dados["margem"],
                progresso=lambda mensagem: atualizar_job(
                    job_id, mensagem=mensagem
                ),
            )

            atualizar_job(
                job_id,
                status="completed",
                mensagem="PDF pronto.",
                resultado={
                    "paginas": resultado["paginas"],
                    "tamanho_bytes": resultado["tamanho_bytes"],
                    "paginas_com_margens_preservadas": (
                        resultado["paginas_com_margens_preservadas"]
                    ),
                },
            )

        except Exception as erro:
            atualizar_job(
                job_id,
                status="failed",
                mensagem=str(erro),
            )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/jobs", status_code=202)
async def criar_job(
    arquivo: UploadFile = File(...),
    separar_duplas: bool = Form(False),
    corrigir_inclinacao: bool = Form(False),
    recortar_margens: bool = Form(False),
    uniformizar: bool = Form(False),
    ocr: bool = Form(False),
    dpi: int = Form(250),
    idioma: str = Form("por"),
    corte: float | None = Form(None),
    margem: float = Form(4.0),
):
    nome = Path(arquivo.filename or "entrada.pdf").name
    extensao = Path(nome).suffix.lower()

    if extensao not in EXTENSOES:
        raise HTTPException(400, "Formato de arquivo não suportado.")

    opcoes = {
        "separar_duplas": separar_duplas,
        "corrigir_inclinacao": corrigir_inclinacao,
        "recortar_margens": recortar_margens,
        "uniformizar": uniformizar,
        "ocr": ocr,
    }

    if not any(opcoes.values()):
        raise HTTPException(400, "Selecione pelo menos uma operação.")

    if not 150 <= dpi <= 400:
        raise HTTPException(400, "O DPI deve ficar entre 150 e 400.")

    if corte == 0:
        corte = None

    if corte is not None and not 40 <= corte <= 60:
        raise HTTPException(400, "O corte deve ficar entre 40% e 60%.")

    if not 1 <= margem <= 15:
        raise HTTPException(400, "A margem deve ficar entre 1% e 15%.")

    job_id = uuid.uuid4().hex
    diretorio = RESULTADOS_DIR / job_id
    diretorio.mkdir(parents=True, exist_ok=True)

    entrada = diretorio / f"entrada{extensao}"
    saida = diretorio / "resultado.pdf"

    tamanho = 0

    try:
        with entrada.open("wb") as destino:
            while True:
                bloco = await arquivo.read(1024 * 1024)
                if not bloco:
                    break

                tamanho += len(bloco)

                if tamanho > MAX_UPLOAD:
                    raise HTTPException(
                        413,
                        "O arquivo ultrapassa o limite de 100 MiB.",
                    )

                destino.write(bloco)

    except Exception:
        shutil.rmtree(diretorio, ignore_errors=True)
        raise

    finally:
        await arquivo.close()

    with LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "mensagem": "Aguardando processamento.",
            "diretorio": str(diretorio),
            "entrada": str(entrada),
            "saida": str(saida),
            "opcoes": opcoes,
            "dpi": dpi,
            "idioma": idioma,
            "corte": corte,
            "margem": margem,
        }

    threading.Thread(
        target=executar_job,
        args=(job_id,),
        daemon=True,
    ).start()

    return {
        "id": job_id,
        "status_url": f"/api/jobs/{job_id}",
    }


@app.get("/api/jobs/{job_id}")
def consultar_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)

        if not job:
            raise HTTPException(404, "Tarefa não encontrada.")

        resposta = {
            "id": job_id,
            "status": job["status"],
            "mensagem": job["mensagem"],
        }

        if "resultado" in job:
            resposta["resultado"] = job["resultado"]

        if job["status"] == "completed":
            resposta["download_url"] = (
                f"/api/jobs/{job_id}/download"
            )

        return resposta


@app.get("/api/jobs/{job_id}/download")
def baixar_resultado(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)

        if not job:
            raise HTTPException(404, "Tarefa não encontrada.")

        if job["status"] != "completed":
            raise HTTPException(
                409,
                "O processamento ainda não terminou.",
            )

        caminho = Path(job["saida"])

    if not caminho.is_file():
        raise HTTPException(404, "Arquivo processado não encontrado.")

    return FileResponse(
        caminho,
        media_type="application/pdf",
        filename="pdf-processado.pdf",
    )
