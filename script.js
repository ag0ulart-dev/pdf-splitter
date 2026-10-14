```javascript
const arquivo = document.getElementById("arquivo");
const nomeArquivo = document.getElementById("nome-arquivo");
const botaoDividir = document.getElementById("dividir");
const status = document.getElementById("status");

arquivo.addEventListener("change", () => {
    const pdf = arquivo.files[0];

    status.textContent = "";

    if (!pdf) {
        nomeArquivo.textContent = "Nenhum arquivo selecionado.";
        botaoDividir.disabled = true;
        return;
    }

    if (
        pdf.type !== "application/pdf" &&
        !pdf.name.toLowerCase().endsWith(".pdf")
    ) {
        nomeArquivo.textContent = "Arquivo inválido.";
        botaoDividir.disabled = true;
        status.textContent = "Selecione um arquivo PDF.";
        return;
    }

    nomeArquivo.textContent =
        `${pdf.name} · ${(pdf.size / 1048576).toFixed(2)} MB`;

    botaoDividir.disabled = false;
});

botaoDividir.addEventListener("click", async () => {
    const arquivoPDF = arquivo.files[0];

    if (!arquivoPDF) return;

    botaoDividir.disabled = true;

    try {
        if (!window.PDFLib || !window.JSZip) {
            throw new Error("As bibliotecas não foram carregadas.");
        }

        status.textContent = "Lendo o PDF...";

        const original = await PDFLib.PDFDocument.load(
            await arquivoPDF.arrayBuffer()
        );

        const total = original.getPageCount();
        const zip = new JSZip();

        for (let i = 0; i < total; i++) {
            status.textContent =
                `Separando página ${i + 1} de ${total}...`;

            const novoPDF = await PDFLib.PDFDocument.create();
            const [pagina] = await novoPDF.copyPages(original, [i]);

            novoPDF.addPage(pagina);

            zip.file(
                `pagina-${String(i + 1).padStart(3, "0")}.pdf`,
                await novoPDF.save()
            );
        }

        status.textContent = "Gerando arquivo ZIP...";

        const resultado = await zip.generateAsync({ type: "blob" });
        const url = URL.createObjectURL(resultado);
        const link = document.createElement("a");

        link.href = url;
        link.download =
            `${arquivoPDF.name.replace(/\.pdf$/i, "")}-paginas.zip`;

        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => URL.revokeObjectURL(url), 1000);

        status.textContent = `Concluído! ${total} páginas separadas.`;
    } catch (erro) {
        console.error(erro);
        status.textContent =
            "Erro ao processar o PDF. Confira sua conexão e se o arquivo está íntegro.";
    } finally {
        botaoDividir.disabled = false;
    }
});
```
