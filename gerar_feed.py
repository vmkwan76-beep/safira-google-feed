"""Gera o feed de produtos do Google Merchant Center da Safira Armarinhos.

Uma oferta por variação (cor), agrupadas pelo produto-pai (item_group_id),
com foto da própria cor, GTIN, marca real e no máximo 10 fotos extras.

Fonte dos dados: o próprio site (somente leitura) — a lista de produtos vem
do feed da VNDA (products_google.rss) e os dados de cada cor vêm do JSON de
variações embutido na página do produto. Nada no site é alterado.

Uso: python gerar_feed.py [saida.xml] [--limite N]
"""
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

SITE = "https://www.safiraarmarinhos.com.br"
FEED_VNDA = SITE + "/products_google.rss"
UA = "Mozilla/5.0 (compatible; SafiraFeedBot/1.0; +https://www.safiraarmarinhos.com.br)"
MAX_FOTOS_EXTRAS = 10
CODIGO_LOJA = "MATRIZ"  # código da loja no Perfil da Empresa (Praça Generoso Marques)
TRABALHADORES = 4

# Marcas procuradas no nome do produto (o cadastro costuma terminar com a marca).
# Ordem importa: a mais específica primeiro. Chave sem acento, minúscula.
MARCAS_NO_TITULO = [
    ("linhas corrente", "Linhas Corrente"), ("roma aviamentos", "Roma"),
    ("mestres sirgueiros", "Mestres Sirgueiros"), ("botoes e cia", "Botões e Cia"),
    ("toke e crie", "Toke e Crie"), ("santa margarida", "Santa Margarida"),
    ("almeida lima", "Almeida Lima"), ("tek bond", "Tekbond"), ("tekbond", "Tekbond"),
    ("sao jose", "São José"), ("santa fe", "Santa Fé"), ("di pietra", "Di Pietra"),
    ("circulo", "Círculo"), ("pingouin", "Pingouin"), ("euroroma", "EuroRoma"),
    ("acrilex", "Acrilex"), ("supremo", "Supremo"), ("anchor", "Anchor"), ("cisne", "Linhas Corrente"),
    ("dohler", "Döhler"), ("kohatsu", "Kohatsu"), ("artepunto", "Artepunto"), ("nybc", "NYBC"),
    ("botelho", "Botelho"), ("valletex", "Valletex"), ("progresso", "Progresso"),
    ("peripan", "Peripan"), ("jowama", "Jowama"), ("prochownik", "Prochownik"),
    ("pietra", "Di Pietra"), ("fibram", "Fibram"), ("incomfio", "Incomfio"), ("gatte", "Gatte"),
    ("carvalho", "Carvalho"), ("singer", "Singer"), ("scafir", "Scafir"), ("preconiz", "Preconiz"),
    ("lomaer", "Lomaer"), ("fischer", "Fischer"), ("pegamil", "Pegamil"), ("acrilpen", "Acrilpen"),
    ("tupy", "Tupy"), ("gruber", "Gruber"), ("make", "Make+"), ("coats", "Coats"),
]

# Marca do cadastro do site (slug do JSON-LD) -> nome certo
MARCAS = {
    "circulo": "Círculo", "pingouin": "Pingouin", "euroroma": "EuroRoma", "acrilex": "Acrilex",
    "anchor": "Anchor", "corrente": "Linhas Corrente", "linhas-corrente": "Linhas Corrente",
    "sao-jose": "São José", "di-pietra": "Di Pietra", "santa-fe": "Santa Fé",
    "roma-aviamentos": "Roma", "santa-margarida": "Santa Margarida", "nybc": "NYBC",
    "dohler": "Döhler",
}


def sem_acento(texto):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def descobrir_marca(titulo, marca_site):
    t = " " + re.sub(r"[^a-z0-9]+", " ", sem_acento(titulo).lower()) + " "
    for chave, nome in MARCAS_NO_TITULO:
        if f" {chave} " in t:
            return nome
    m = sem_acento(marca_site or "").strip().lower()
    if not m or m == "safira armarinhos":
        return ""
    return MARCAS.get(m, (marca_site or "").replace("-", " ").strip().title())

# Palavras que ficam em minúsculas no meio do título
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "com", "para", "em", "a", "o"}
# Siglas/unidades que ficam como estão
MANTER = {"G", "KG", "M", "MM", "CM", "MT", "MTS", "N.", "UN", "ML", "TEX"}
ACENTOS = {"LA": "Lã", "CIRCULO": "Círculo", "LINHA": "Linha", "BEBE": "Bebê",
           "CROCHE": "Crochê", "AGULHA": "Agulha", "LA.": "Lã"}


def baixar(url, tentativas=4):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dados = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    dados = gzip.decompress(dados)
                return dados.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            if t == tentativas - 1:
                raise
            time.sleep(5 * (t + 1))


def titulo_bonito(texto):
    """'LA MOLLET 100G CIRCULO' -> 'Lã Mollet 100g Círculo'."""
    palavras = []
    for i, p in enumerate(texto.split()):
        up = p.upper()
        if up in ACENTOS:
            palavras.append(ACENTOS[up])
        elif re.fullmatch(r"\d+([.,]\d+)?(G|KG|M|MM|CM|MT|MTS|ML)", up):
            palavras.append(p.lower())  # 100g, 178m
        elif up in MANTER:
            palavras.append(up)
        elif re.search(r"\d", p):
            palavras.append(up)  # códigos de cor, N.4, 1/2
        elif i > 0 and p.lower() in MINUSCULAS:
            palavras.append(p.lower())
        else:
            palavras.append(p.capitalize())
    return " ".join(palavras)


def gtin_valido(codigo):
    codigo = (codigo or "").strip()
    if not re.fullmatch(r"\d{8}|\d{12,14}", codigo) or set(codigo) == {"0"}:
        return False
    digitos = [int(c) for c in codigo]
    soma = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digitos[:-1])))
    return (10 - soma % 10) % 10 == digitos[-1]


def ler_lista_vnda():
    """Lista de produtos (id, link, descrição) a partir do feed da VNDA."""
    xml = baixar(FEED_VNDA)
    produtos = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(nome):
            m = re.search(rf"<{nome}>(.*?)</{nome}>", item, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        produtos.append({"id": tag("id"), "link": tag("link"), "titulo": tag("title"),
                         "descricao": tag("description")})
    return produtos


def ler_produto(prod):
    pagina = baixar(prod["link"])
    m = re.search(r"id='vndajs' data-variant=\"(.*?)\"", pagina, re.S)
    if not m:
        return prod, None, "sem dados de variação"
    variantes = json.loads(html.unescape(m.group(1)))

    marca = ""
    for bloco in re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', pagina, re.S):
        try:
            d = json.loads(bloco)
        except ValueError:
            continue
        if d.get("@type") == "Product":
            marca = (d.get("brand") or {}).get("name", "") if isinstance(d.get("brand"), dict) else ""
    marca = descobrir_marca(prod["titulo"], marca)

    # Fotos por SKU (miniaturas da galeria marcadas com data-skus)
    fotos_sku = {}
    fotos_gerais = []
    for bloco in re.findall(r'<div class="image-thumb swiper-slide"(.*?)</figure>', pagina, re.S):
        img = re.search(r'(?:data-src|src)="(https://cdn\.vnda\.com\.br/[^"]+)"', bloco)
        if not img:
            continue
        url = re.sub(r"cdn\.vnda\.com\.br/\d+x/", "cdn.vnda.com.br/", img.group(1))
        skus = re.search(r'data-skus="([^"]*)"', bloco)
        if skus:
            for s in filter(None, skus.group(1).split(",")):
                fotos_sku.setdefault(s.strip(), []).append(url)
        else:
            fotos_gerais.append(url)

    cats = re.search(r'"item_category":"([^"]*)"(?:,"item_category2":"([^"]*)")?', pagina)
    prod["categorias"] = [c for c in (cats.groups()[::-1] if cats else []) if c and c != "toda-a-loja"]

    titulo_m = re.search(r"<title>(.*?)</title>", pagina, re.S)
    prod["titulo_site"] = html.unescape(titulo_m.group(1).strip()) if titulo_m else ""
    prod["marca"] = marca
    prod["fotos_sku"] = fotos_sku
    prod["fotos_gerais"] = list(dict.fromkeys(fotos_gerais))
    return prod, variantes, None


# (palavras no nome, sem acento) -> (id da categoria Google, complemento do título)
# Primeira regra que casar vence. IDs da taxonomia oficial pt-BR do Google.
CATEGORIAS_GOOGLE = [
    (("agulha", "croch"), 6127, ""), (("agulha", "trico"), 6139, ""),
    (("agulha", "tunisiana"), 6127, ""), (("agulha", "maquina"), 4579, ""),
    (("agulha", "circular"), 6139, ""), (("agulha",), 5992, ""),
    (("tesoura",), 504641, ""), (("alfinete de seguranca",), 6101, ""), (("alfinete",), 6159, ""),
    (("marcador",), 6160, ""), (("abridor de casa",), 6161, ""), (("pistola",), 4073, ""),
    (("cola",), 503745, ""), (("lapis",), 16, ""), (("barbante",), 2669, "para Crochê"),
    (("fio de malha",), 2669, "para Crochê"), (("la ",), 2669, "para Crochê e Tricô"),
    (("fio ",), 2669, "para Crochê e Tricô"), (("novelo",), 2669, "para Crochê e Tricô"),
    (("meada",), 49, "para Bordado"), (("mouline",), 49, "para Bordado"),
    (("linha",), 49, ""), (("retros",), 49, ""),
    (("fita",), 505419, ""), (("vies",), 505412, ""), (("passamanaria",), 505412, ""),
    (("soutache",), 505412, ""), (("renda",), 505412, ""), (("bordado ingles",), 505412, ""),
    (("passafita",), 505412, ""), (("sianinha",), 505412, ""), (("elastico",), 6146, ""),
    (("botao",), 4226, ""), (("botoes",), 4226, ""), (("ziper",), 4174, ""),
    (("fecho de bolsa",), 6145, ""), (("fecho",), 4174, ""), (("ilhos",), 505409, ""),
    (("argola",), 505409, ""), (("mosquetao",), 505408, ""), (("lantejoula",), 505410, ""),
    (("glitter",), 505410, ""), (("strass",), 5982, ""), (("pompom",), 505379, ""),
    (("olhos",), 505379, ""), (("laco",), 505413, ""), (("lacinho",), 505413, ""),
    (("entremeio",), 32, ""), (("contas",), 32, ""), (("fibra",), 505407, ""),
    (("enchimento",), 505407, ""), (("refil de almofada",), 505407, ""), (("feltro",), 47, ""),
    (("tecido",), 47, ""), (("tricoline",), 47, ""), (("eva",), 6117, ""),
    (("corante",), 505415, ""), (("tinta",), 505417, ""), (("caneta para tecido",), 505417, ""),
    (("toalha de mesa",), 4143, ""), (("kit",), 505370, ""),
]
CATEGORIA_PADRAO = 16  # Artes e entretenimento > Hobbies e artes > Arte e artesanato


def classificar(titulo):
    t = " " + re.sub(r"[^a-z0-9]+", " ", sem_acento(titulo).lower()) + " "
    for chaves, gid, complemento in CATEGORIAS_GOOGLE:
        # cada chave casa no início de palavra ("cola" não casa "escolar"); "la " exige palavra inteira
        if all(re.search(r"\b" + re.escape(c.strip()) + (r"\b" if c.endswith(" ") else ""), t) for c in chaves):
            return gid, complemento
    return CATEGORIA_PADRAO, ""


def tipo_produto(categorias):
    """['croche-e-trico', 'fios-para-inverno'] -> 'Crochê e Tricô > Fios para Inverno'."""
    nomes = {"croche": "Crochê", "trico": "Tricô", "la": "Lã", "eva": "EVA", "verao": "Verão"}
    return " > ".join(
        " ".join(nomes.get(w, w if w in ("e", "de", "para", "da", "do") else w.capitalize())
                 for w in c.split("-"))
        for c in categorias) or None


def destaques(descricao):
    """Linhas '*Composição: ...' do bloco DADOS TÉCNICOS da descrição (máx. 6)."""
    itens = []
    for linha in descricao.splitlines():
        linha = linha.strip()
        if linha.startswith("*") and "foto" not in linha.lower():
            texto = linha.lstrip("* ").strip()
            if 3 < len(texto) <= 150:
                itens.append(texto)
    return itens[:6]


def carregar_frete(caminho="frete.json"):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)["estados"]
    except (OSError, ValueError, KeyError):
        return {}


FRETE = carregar_frete()


def frete_item(peso_kg):
    """g:shipping por faixa de CEP (estado) com o preço/prazo da opção mais barata."""
    if not FRETE or not peso_kg:
        return []
    regras = []
    for estado in FRETE.values():
        faixa = next((f for f in estado["faixas"] if peso_kg <= f["ate_kg"]), None)
        if not faixa:
            return []  # acima da maior faixa: usa a política de frete da conta
        for prefixo in estado["cep"]:
            regras.append({"country": "BR", "postal_code": prefixo,
                           "price": f'{faixa["preco"]:.2f} BRL',
                           "min_handling_time": 0, "max_handling_time": 1,
                           "min_transit_time": faixa["dias_min"],
                           "max_transit_time": faixa["dias_max"]})
    return regras


def nome_cor(nome_variante, titulo_pai):
    """'LA MOLLET 100G CIRCULO - 010 - BRANCO' -> '010 BRANCO'.

    Também tira o nome do produto repetido no início ('LA POP SHOCK 100G 5990 BOSQUE'
    -> '5990 BOSQUE') e o hífen grudado no código ('8099 -DALMATA', '5009-SUSSURRO')."""
    resto = nome_variante
    if resto.upper().startswith(titulo_pai.upper()):
        resto = resto[len(titulo_pai):]
    resto = re.sub(r"\s+-\s*|\s*-\s+", " ", resto.strip(" -*"))
    resto = re.sub(r"^\*\s*|\s\*\s", " ", resto).strip()
    resto = re.sub(r"(?<=\d)-|(?<=\d[A-Za-z])-", " ", resto)
    do_pai = set(re.findall(r"[a-z0-9]+", sem_acento(titulo_pai).lower())) | {"cor"}
    palavras = resto.split()
    while len(palavras) > 1:
        w = sem_acento(palavras[0]).lower().strip(".,")
        if w in do_pai or w + "s" in do_pai or w.rstrip("s") in do_pai:
            palavras.pop(0)
        else:
            break
    return " ".join(palavras)


NUM = r"\d+(?:[.,]\d+)?"


def _num(texto):
    return float(texto.replace(".", "").replace(",", ".")) if re.fullmatch(r"\d{1,3}(\.\d{3})+", texto)         else float(texto.replace(",", "."))


def _fmt(x):
    return (f"{x:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


def ficha_tecnica(descricao):
    """Lê o bloco DADOS TÉCNICOS da descrição: tex, composição, metros, gramas, agulhas.
    Só devolve o que está escrito — nada é deduzido."""
    partes = re.split(r"DADOS T[ÉE]CNICOS", descricao, flags=re.I)
    if len(partes) < 2:
        return {}
    linhas = [l.replace(" ", " ").strip().lstrip("*").strip() for l in partes[1].splitlines()]
    linhas = [l for l in linhas if l and "foto" not in l.lower()]
    f = {}
    for l in linhas:
        b = sem_acento(l).lower()
        valor = l.split(":", 1)[1].strip() if ":" in l else l
        if "tex" in b and "tex" not in f:
            m = re.search(r"(\d+)\s*\)?\s*tex|tex\s*:?\s*(\d+)", b)
            if m:
                f["tex"] = m.group(1) or m.group(2)
        if b.startswith("composi") and "composicao" not in f:
            f["composicao"] = valor.rstrip(". ")
        if re.search(r"comprimento|metragem|metros|medidas|^peso|quantidade", b):
            m = re.search(r"(" + NUM + r")\s*(?:m|mt|mts|metros)\b(?!m)", b)
            if m and "metros" not in f:
                f["metros"] = _num(m.group(1))
            m = re.search(r"(" + NUM + r")\s*(kg|g|gr|gramas)\b", b)
            if m and "gramas" not in f:
                f["gramas"] = _num(m.group(1)) * (1000 if m.group(2) == "kg" else 1)
        if ("agulh" in b or "ag." in b) and "malha" not in b:
            faixa = re.search(r"(" + NUM + r")\s*(?:mm)?\s*a\s*(" + NUM + r")\s*mm", b)
            unica = re.search(r"(" + NUM + r")\s*mm", b)
            txt = (f"{_fmt(_num(faixa.group(1)))} a {_fmt(_num(faixa.group(2)))} mm" if faixa
                   else f"{_fmt(_num(unica.group(1)))} mm" if unica else None)
            if txt:
                if "croch" in b:
                    f.setdefault("agulha_croche", txt)
                elif "trico" in b:
                    f.setdefault("agulha_trico", txt)
                else:
                    f.setdefault("agulha", txt)
    return f


# (trecho sem acento, minúsculo) -> nome da fibra. Ordem importa: o mais específico primeiro.
FIBRAS = [
    ("baby alpaca", "Baby Alpaca"), ("alpaca", "Alpaca"), ("algodao mercerizado", "Algodão Mercerizado"),
    ("algodao penteado", "Algodão Mercerizado"), ("algodao reciclado", "Algodão Reciclado"),
    ("algodao", "Algodão"), ("acrilico", "Acrílico"), ("poliester", "Poliéster"),
    ("poliamida", "Poliamida"), ("polipropileno", "Polipropileno"),
    ("prolipropileno", "Polipropileno"), ("viscose", "Viscose"), ("elastano", "Elastano"),
    ("juta", "Juta"), ("rafia", "Papel de Ráfia"), (r"metal\w*", "Fibra Metálica"), ("cera", "Cera"),
    ("outras fibras", "outras fibras"), ("la", "Lã"),
]


def fibras(composicao):
    """'90% Acrilico e 10% POLIAMIDA' -> [(90.0, 'Acrílico'), (10.0, 'Poliamida')].
    Devolve [] se alguma parte não for uma fibra conhecida ou se não somar 100%."""
    if not composicao:
        return []
    out = []
    for pct, trecho in re.findall(r"(\d+(?:[.,]\d+)?)\s*[%$]\s*([^\d|/]*)", composicao):
        t = re.sub(r"[^a-z ]", " ", sem_acento(trecho).lower())
        t = re.sub(r"\b(e|de|no minimo|cor)\b", " ", t)
        nome = next((n for k, n in FIBRAS if re.search(r"\b" + k + r"\b", t)), None)
        if not nome:
            return []
        out.append((_num(pct), nome))
    if not out or abs(sum(p for p, _ in out) - 100) > 1:
        return []
    return out


def composicao_limpa(composicao):
    """Texto do atributo g:material: padronizado quando as fibras são conhecidas."""
    lista = fibras(composicao)
    if lista:
        return ", ".join(f"{p:g}".replace(".", ",") + f"% {n}" for p, n in lista)
    texto = re.sub(r"(?i)^composi[cç][aã]o\s*:?\s*", "", composicao or "").strip(" |.")
    return texto or None


def material_curto(composicao):
    """'90% Acrílico e 10% Poliamida' -> 'Acrílico e Poliamida'; '100% Algodão' -> '100% Algodão'.
    Se o cadastro estiver incompleto ou com fibra desconhecida, devolve None."""
    nomes = list(dict.fromkeys(n for _, n in fibras(composicao)))
    if not nomes:
        return None
    if len(nomes) == 1:
        return f"100% {nomes[0]}"
    return " e ".join(nomes) if len(nomes) == 2 else f"{nomes[0]}, {nomes[1]} e outras"


def detalhes_produto(ficha):
    rotulos = [("composicao", "Composição"), ("metros", "Metragem"), ("gramas", "Peso"),
               ("tex", "Espessura (TEX)"), ("agulha_croche", "Agulha de crochê"),
               ("agulha_trico", "Agulha de tricô"), ("agulha", "Agulha recomendada")]
    out = []
    for chave, rotulo in rotulos:
        if chave in ficha:
            v = ficha[chave]
            v = (f"{_fmt(v)} m" if chave == "metros" else f"{_fmt(v)} g" if chave == "gramas"
                 else composicao_limpa(v) if chave == "composicao" else str(v))
            out.append({"section_name": "Dados técnicos", "attribute_name": rotulo, "attribute_value": v})
    return out


def titulo_fio(base, ficha, cor, complemento):
    """Nome + gramatura + marca primeiro (como a cliente busca), depois metragem,
    material, cor e uso. Corta os atributos extras se passar de 150 caracteres."""
    if "gramas" in ficha and not re.search(r"\d\s*(g|kg)\b", base.lower()):
        base = f"{base} {_fmt(ficha['gramas'])}g"
    extras = []
    # em kit a metragem da ficha é de cada novelo — não vai no título para não confundir
    if ("metros" in ficha and not re.search(r"\d\s*(m|mt|mts|metros)\b", base.lower())
            and not base.lower().startswith("kit")):
        extras.append(f"{_fmt(ficha['metros'])}m")
    mat = material_curto(ficha.get("composicao"))
    if mat:
        extras.append(mat)
    fim = (f" - Cor {cor}" if cor else "") + complemento
    while extras and len(base + "".join(" - " + e for e in extras) + fim) > 150:
        extras.pop()
    return (base + "".join(" - " + e for e in extras) + fim)[:150]


def montar_itens(prod, variantes):
    itens = []
    unica = len(variantes) == 1
    base = titulo_bonito(prod["titulo"])
    descricao = re.sub(r"\n{3,}", "\n\n", prod["descricao"]).strip()[:5000]
    categoria_google, complemento = classificar(prod["titulo"])
    complemento = f" - {complemento}" if complemento and "croch" not in sem_acento(base).lower() else ""
    tipo = tipo_produto(prod.get("categorias", []))
    realces = destaques(prod["descricao"])
    fio = categoria_google == 2669
    ficha = ficha_tecnica(prod["descricao"]) if fio else {}
    for v in variantes:
        cor = "" if unica else nome_cor(v.get("name", ""), prod["titulo"])
        if fio:
            titulo = titulo_fio(base, ficha, titulo_bonito(cor) if cor else "", complemento)
        else:
            titulo = base
            if cor:
                titulo = f"{base} Cor {titulo_bonito(cor)}"
            titulo += complemento
        foto = v.get("image_url") or ""
        if foto.startswith("//"):
            foto = "https:" + foto
        extras = [f for f in prod["fotos_sku"].get(v["sku"], []) if f.split("?")[0] != foto.split("?")[0]]
        extras += [f for f in prod["fotos_gerais"] if f not in extras]
        extras = extras[:MAX_FOTOS_EXTRAS]
        if not foto and extras:
            foto = extras.pop(0)
        if not foto:
            continue
        disponivel = v.get("available") and (v.get("available_quantity") or 0) > 0
        preco = float(v.get("price") or 0)
        promo = float(v.get("sale_price") or 0)
        if preco <= 0:
            continue
        item = {
            "id": v["sku"],
            "item_group_id": None if unica else prod["id"],
            "title": titulo[:150],
            "description": descricao or titulo,
            "link": prod["link"] if unica else f'{prod["link"]}?sku={v["sku"]}',
            "image_link": foto,
            "additional_image_link": extras,
            "availability": "in_stock" if disponivel else "out_of_stock",
            "price": f"{preco:.2f} BRL",
            "sale_price": f"{promo:.2f} BRL" if 0 < promo < preco else None,
            "condition": "new",
            "brand": prod["marca"] or None,
            "gtin": v.get("barcode") if gtin_valido(v.get("barcode")) else None,
            "mpn": None,
            "color": titulo_bonito(cor) if cor else None,
            "shipping_weight": f'{v["weight"]:.3f} kg' if v.get("weight") else None,
            "google_product_category": categoria_google,
            "product_type": tipo,
            "product_highlight": realces,
            "material": (composicao_limpa(ficha.get("composicao")) or "")[:200] if fio else None,
            "product_detail": detalhes_produto(ficha) if fio else [],
            "shipping": frete_item(v.get("weight")),
            "_qtd": int(v.get("available_quantity") or 0) if disponivel else 0,
        }
        if not item["gtin"]:
            if item["brand"]:
                item["mpn"] = v["sku"]
            else:
                item["identifier_exists"] = "no"
        itens.append(item)
    return itens


def gerar_xml(itens):
    linhas = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">', "<channel>",
              "<title>Safira Armarinhos</title>", f"<link>{SITE}</link>",
              "<description>Safira Armarinhos</description>"]
    for it in itens:
        linhas.append("<item>")
        for chave, valor in it.items():
            if chave.startswith("_") or valor in (None, "", []):
                continue
            tag = chave if chave in ("title", "description", "link") else "g:" + chave
            for v in (valor if isinstance(valor, list) else [valor]):
                if isinstance(v, dict):
                    filhos = "".join(f"<g:{k}>{escape(str(x))}</g:{k}>" for k, x in v.items())
                    linhas.append(f"<{tag}>{filhos}</{tag}>")
                else:
                    linhas.append(f"<{tag}>{escape(str(v))}</{tag}>")
        linhas.append("</item>")
    linhas += ["</channel>", "</rss>"]
    return "\n".join(linhas)


def gerar_local(itens):
    """Feed de inventário local da loja física (mesmo estoque do site)."""
    local = [{"store_code": CODIGO_LOJA, "id": it["id"], "availability": it["availability"],
              "quantity": it["_qtd"], "price": it["price"], "sale_price": it["sale_price"]}
             for it in itens]
    return gerar_xml(local)


def main():
    args = sys.argv[1:]
    saida = next((a for a in args if not a.startswith("--") and not a.isdigit()), "feed.xml")
    limite = int(args[args.index("--limite") + 1]) if "--limite" in args else None

    produtos = ler_lista_vnda()
    if limite:
        produtos = produtos[:limite]
    print(f"{len(produtos)} produtos no feed da VNDA", flush=True)

    itens, erros = [], []
    with ThreadPoolExecutor(TRABALHADORES) as ex:
        for n, fut in enumerate(ex.map(lambda p: _seguro(p), produtos), 1):
            prod, variantes, erro = fut
            if erro:
                erros.append((prod["id"], prod["link"], erro))
            else:
                itens.extend(montar_itens(prod, variantes))
            if n % 100 == 0:
                print(f"  {n}/{len(produtos)} produtos lidos, {len(itens)} ofertas", flush=True)

    if len(produtos) > 50 and len(erros) > len(produtos) * 0.2:
        sys.exit(f"Muitos erros ({len(erros)}); feed NÃO gravado para não derrubar o catálogo.")

    with open(saida, "w", encoding="utf-8") as f:
        f.write(gerar_xml(itens))
    saida_local = os.path.join(os.path.dirname(saida), "inventario_local.xml")
    with open(saida_local, "w", encoding="utf-8") as f:
        f.write(gerar_local(itens))
    em_estoque = sum(1 for i in itens if i["availability"] == "in_stock")
    print(f"OK: {len(itens)} ofertas ({em_estoque} em estoque), {len(erros)} produtos com erro -> {saida}")
    for e in erros[:20]:
        print("  erro:", *e)


def _seguro(prod):
    try:
        return ler_produto(prod)
    except Exception as e:  # noqa: BLE001
        return prod, None, f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
