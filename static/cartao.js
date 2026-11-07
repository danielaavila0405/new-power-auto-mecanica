
function taxaCartaoSelecionada() {
    if (document.querySelector('[name="pagamento"]').value !== 'Cartão') return 0;
    const tipo = document.getElementById('cartao_tipo').value;
    const parcelas = tipo === 'credito' ? document.getElementById('cartao_parcelas').value : '1';
    return (taxasCartao[tipo] || {})[parcelas] || 0;
}

document.addEventListener('DOMContentLoaded', () => {
    const pagamento = document.querySelector('[name="pagamento"]');
    const painel = document.getElementById('cartao-opcoes');
    const tipo = document.getElementById('cartao_tipo');
    const parcelas = document.getElementById('cartao_parcelas');
    const total = document.getElementById('totalOS') || document.getElementById('totalGeral');
    const moeda = valor => valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

    function resumo() {
        const taxa = taxaCartaoSelecionada();
        const bruto = Number(total.textContent.replace(/[^0-9,-]/g, '').replace(',', '.')) || 0;
        const centavos = Math.round((bruto + Number.EPSILON) * 100);
        const desconto = Math.round(centavos * Math.round(taxa * 100) / 10000) / 100;
        document.getElementById('cartao-taxa').textContent = tipo.value ? taxa.toLocaleString('pt-BR', {minimumFractionDigits: 2}) + '%' : 'Selecione a modalidade';
        document.getElementById('cartao-desconto').textContent = tipo.value ? moeda(desconto) : '—';
        document.getElementById('cartao-liquido').textContent = tipo.value ? moeda(bruto - desconto) : '—';
    }

    function atualizar() {
        const cartao = pagamento.value === 'Cartão';
        const credito = cartao && tipo.value === 'credito';
        painel.hidden = !cartao;
        tipo.disabled = !cartao;
        tipo.required = cartao;
        document.getElementById('cartao-parcelas').hidden = !credito;
        parcelas.disabled = !credito;
        parcelas.required = credito;
        calcularTotais();
        resumo();
    }

    [pagamento, tipo, parcelas].forEach(campo => campo.addEventListener('change', atualizar));
    new MutationObserver(resumo).observe(total, {childList: true, characterData: true, subtree: true});
    atualizar();
});
