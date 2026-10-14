```javascript
const arquivo = document.getElementById("arquivo");
const nomeArquivo = document.getElementById("nome-arquivo");
const botaoDividir = document.getElementById("dividir");
const status = document.getElementById("status");

arquivo.addEventListener("change", () => {
    const pdf = arquivo.files[0];

    if (!pdf) {
        nomeArquivo.textContent = "Nenhum arquivo selecionado.";
        botaoDividir.disabled = true;
        status.textContent = "";
        return;
    }

    if (pdf.type !== "application/pdf" &&
        !pdf.name.toLowerCase().endsWith(".pdf")) {
        nomeArquivo.textContent = "Arquivo inválido.";
        botaoDividir.disabled = true;
        status.textContent = "Selecione um arquivo PDF.";
        return;
    }

    nomeArquivo.textContent =
        `${pdf.name} · ${(pdf.size / (1024 * 1024)).toFixed(2)} MB`;

    botaoDividir.disabled = false;
    status.textContent = "PDF selecionado. Pronto para continuar.";
});

botaoDividir.addEventListener("click", () => {
    status.textContent =
        "A seleção está funcionando. O processamento de páginas será implementado a seguir.";
});
```
