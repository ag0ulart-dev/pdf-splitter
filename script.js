const arquivo = document.getElementById("arquivo");
const nomeArquivo = document.getElementById("nome-arquivo");
const opcaoSeparar = document.getElementById("separar-duplas");
const botao = document.getElementById("processar");
const status = document.getElementById("status");

pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const operacoes = {
    "separar-duplas": separarPaginasDuplas
};

function atualizarBotao() {
    botao.disabled = !arquivo.files.length ||
        !Object.keys(operacoes).some(id =>
            document.getElementById(id)?.checked
        );
}

arquivo.addEventListener("change", () => {
    const pdf = arquivo.files[0];

    status.textContent = "";

    if (!pdf) {
        nomeArquivo.textContent = "Nenhum arquivo selecionado.";
    } else if (
        pdf.type !== "application/pdf" &&
        !pdf.name.toLowerCase().endsWith(".pdf")
    ) {
        nomeArquivo.textContent = "Arquivo inválido.";
        arquivo.value = "";
    } else {
        nomeArquivo.textContent =
            `${pdf.name} · ${(pdf.size / 1048576).toFixed(2)} MB`;
    }

    atualizarBotao();
});

opcaoSeparar.addEventListener("change", atualizarBotao);

function canvasParaJpeg(canvas) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(blob => {
            if (blob) resolve(blob);
            else reject(new Error("Não foi possível preparar a página."));
        }, "image/jpeg", 0.92);
    });
}

async function separarPaginasDuplas(pdf, saida) {
    const total = pdf.numPages;
    const escala = 2;

    for (let numero = 1; numero <= total; numero++) {
        status.textContent =
            `Separando página ${numero} de ${total}...`;

        const pagina = await pdf.getPage(numero);
        const viewport = pagina.getViewport({ scale: escala });

        const canvas = document.createElement("canvas");
        const contexto = canvas.getContext("2d", {
            alpha: false
        });

        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);

        await pagina.render({
            canvasContext: contexto,
            viewport,
            background: "#ffffff"
        }).promise;

        const larguraEsquerda = Math.floor(canvas.width / 2);
        const larguraDireita = canvas.width - larguraEsquerda;

        for (const lado of [
            { x: 0, largura: larguraEsquerda },
            { x: larguraEsquerda, largura: larguraDireita }
        ]) {
            const recorte = document.createElement("canvas");
            recorte.width = lado.largura;
            recorte.height = canvas.height;

            recorte.getContext("2d", {
                alpha: false
            }).drawImage(
                canvas,
                lado.x, 0, lado.largura, canvas.height,
                0, 0, lado.largura, canvas.height
            );

            const blob = await canvasParaJpeg(recorte);
            const bytes = new Uint8Array(await blob.arrayBuffer());
            const imagem = await saida.embedJpg(bytes);

            const paginaSaida = saida.addPage([
                lado.largura / escala,
                canvas.height / escala
            ]);

            paginaSaida.drawImage(imagem, {
                x: 0,
                y: 0,
                width: lado.largura / escala,
                height: canvas.height / escala
            });

            recorte.width = 0;
            recorte.height = 0;
        }

        canvas.width = 0;
        canvas.height = 0;
        pagina.cleanup();
    }

    await pdf.destroy();
}

botao.addEventListener("click", async () => {
    const arquivoPDF = arquivo.files[0];
    if (!arquivoPDF) return;

    const selecionadas = Object.keys(operacoes).filter(id =>
        document.getElementById(id)?.checked
    );

    if (!selecionadas.length) {
        status.textContent = "Selecione pelo menos uma operação.";
        return;
    }

    botao.disabled = true;
    arquivo.disabled = true;
    opcaoSeparar.disabled = true;

    let documento = null;

    try {
        if (!window.PDFLib || !window.pdfjsLib) {
            throw new Error("As bibliotecas não foram carregadas.");
        }

        status.textContent = "Abrindo o PDF...";

        documento = await pdfjsLib.getDocument({
            data: new Uint8Array(await arquivoPDF.arrayBuffer())
        }).promise;

        const saida = await PDFLib.PDFDocument.create();

        for (const id of selecionadas) {
            await operacoes[id](documento, saida);
        }

        status.textContent = "Gerando o PDF final...";

        const bytes = await saida.save();
        const blob = new Blob([bytes], {
            type: "application/pdf"
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");

        link.href = url;
        link.download =
            `${arquivoPDF.name.replace(/\.pdf$/i, "")}-separado.pdf`;

        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => URL.revokeObjectURL(url), 2000);

        status.textContent =
            "Concluído! O PDF separado foi baixado.";
    } catch (erro) {
        console.error(erro);
        status.textContent =
            `Não foi possível processar o PDF: ${erro.message}`;
    } finally {
        if (documento) {
            try {
                await documento.destroy();
            } catch (_) {}
        }

        botao.disabled = false;
        arquivo.disabled = false;
        opcaoSeparar.disabled = false;
        atualizarBotao();
    }
});
