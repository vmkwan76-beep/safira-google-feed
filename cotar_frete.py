"""Cota o frete real do site (mesma cotação do "calcular frete" da página do
produto: contrato Correios, Total Express, Olist Envios) por estado e faixa de
peso, e grava frete.json para o gerar_feed.py usar em g:shipping.

Para cada estado e faixa pega a opção MAIS BARATA (preço + prazo dela); quando o
estado tem capital + interior, usa o pior caso (maior preço / maior prazo) para
não anunciar frete abaixo do real.

Uso: python cotar_frete.py [frete.json]
Se a cotação falhar em mais de 20% dos casos, não sobrescreve o arquivo.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

SITE = "https://www.safiraarmarinhos.com.br"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SafiraFeedBot/1.0"

# SKU de referência (Lã Mollet 100g, 0,105 kg); a quantidade simula o peso.
SKU_REF = "985046"
PESO_REF = 0.105
FAIXAS_KG = [0.3, 1.0, 2.0, 5.0]  # g:shipping vale para itens até esse peso

# Estado -> (CEPs de amostra, prefixos de CEP no formato aceito pelo Google)
ESTADOS = {
    "SP": (["01310100", "14010100"], ["01*-19*"]),
    "RJ": (["20040020", "28010000"], ["20*-28*"]),
    "ES": (["29010120"], ["29*"]),
    "MG": (["30130010", "38400100"], ["30*-39*"]),
    "BA": (["40020000", "45000000"], ["40*-48*"]),
    "SE": (["49010000"], ["49*"]),
    "PE": (["50030230", "56300000"], ["50*-56*"]),
    "AL": (["57020000"], ["57*"]),
    "PB": (["58010000"], ["58*"]),
    "RN": (["59025000"], ["59*"]),
    "CE": (["60060100", "63010000"], ["60*-63*"]),
    "PI": (["64000020"], ["64*"]),
    "MA": (["65010000"], ["65*"]),
    "PA": (["66010000", "68500000"], ["660*-688*"]),
    "AP": (["68900073"], ["689*"]),
    "AM": (["69005000"], ["690*-692*", "694*-698*"]),
    "RR": (["69301000"], ["693*"]),
    "AC": (["69900062"], ["699*"]),
    "DF": (["70040010"], ["700*-727*", "730*-736*"]),
    "GO": (["74003010", "75800000"], ["728*-729*", "737*-767*"]),
    "RO": (["76801000"], ["768*-769*"]),
    "TO": (["77001002"], ["77*"]),
    "MT": (["78005000", "78700000"], ["780*-788*"]),
    "MS": (["79002000"], ["79*"]),
    "PR": (["80010000", "86010000"], ["80*-87*"]),
    "SC": (["88010000", "89010000"], ["88*-89*"]),
    "RS": (["90010000", "99010000"], ["90*-99*"]),
}


def cotar(cep, quantidade):
    dados = urllib.parse.urlencode({"sku": SKU_REF, "quantity": quantidade, "zip": cep}).encode()
    req = urllib.request.Request(SITE + "/frete_produto", data=dados, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"})
    for t in range(6):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                resp = json.loads(r.read().decode("utf-8"))
            metodos = resp.get("methods")
            metodos = json.loads(metodos) if isinstance(metodos, str) else (metodos or [])
            opcoes = []
            for m in metodos:
                if m.get("place_id"):  # retirada na loja não é entrega
                    continue
                preco = float(m.get("price") or 0)
                dias = int(m.get("delivery_days") or 0)
                desc = re.search(r"(\d+)\s*dias", m.get("description") or "")
                dias_min = int(desc.group(1)) if desc else dias
                if preco > 0 and dias > 0:
                    opcoes.append((preco, dias, min(dias_min, dias), m.get("name")))
            return min(opcoes) if opcoes else None
        except Exception:  # noqa: BLE001
            time.sleep(5 * (t + 1))
    return None


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else "frete.json"
    tabela, falhas, total = {}, 0, 0
    for uf, (ceps, prefixos) in ESTADOS.items():
        faixas = []
        for kg in FAIXAS_KG:
            qtd = max(1, round(kg / PESO_REF))
            piores = []
            for cep in ceps:
                total += 1
                r = cotar(cep, qtd)
                time.sleep(2)
                if r is None:
                    falhas += 1
                else:
                    piores.append(r)
            if piores:
                faixas.append({
                    "ate_kg": kg,
                    "preco": round(max(p[0] for p in piores), 2),
                    "dias_min": max(p[2] for p in piores),
                    "dias_max": max(p[1] for p in piores),
                    "servico": max(piores)[3],
                })
        tabela[uf] = {"cep": prefixos, "faixas": faixas}
        print(uf, [(f["ate_kg"], f["preco"], f'{f["dias_min"]}-{f["dias_max"]}d') for f in faixas], flush=True)

    if falhas > total * 0.2:
        sys.exit(f"Cotação falhou em {falhas}/{total}; {saida} NÃO foi alterado.")
    with open(saida, "w", encoding="utf-8") as f:
        json.dump({"gerado_em": time.strftime("%Y-%m-%d %H:%M"), "estados": tabela}, f,
                  ensure_ascii=False, indent=1)
    print(f"OK: {total - falhas}/{total} cotações -> {saida}")


if __name__ == "__main__":
    main()
