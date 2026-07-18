import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from dotenv import load_dotenv

#testes 1501727032285855844
#avisos 1501027386857099406

# //////////////////////////////////////////////////////////////|CONFIGURACAO INICIAL|//////////////////////////////////
# Carrega o .env, define token/canal do Discord, emojis dos botoes e arquivos JSON usados pelo bot.
load_dotenv()

NEXUS_BASE = "https://nexustoons.com"
TOKEN = os.getenv("discord_token")
CANAL_AVISOS_ID = os.getenv("DISCORD_CHANNEL_ID")
NEXUS_API_TOKEN = os.getenv("NEXUS_API_TOKEN")
BOTAO_LINK_EMOJI = discord.PartialEmoji(name="sakura", id=1500613669933813943)
EMOJIS_LINKS = {
    "AL": discord.PartialEmoji(name="Anilist", id=1500618413653885180),
    "Nexus": discord.PartialEmoji(name="Nexus", id=1504469618264244414),
    "Dex": discord.PartialEmoji(name="Mangadex", id=1500616508785692875),
    "MAL": discord.PartialEmoji(name="MAL", id=1500618601441398966),
    "Empty": discord.PartialEmoji(name="Empty", id=1503310682228129793),
    "Kuro": discord.PartialEmoji(name="Kuro", id=1523940949011468408),
}
MENCOES_OBRAS = {
    "please don't die": "Please Don't Die",
    "Sanagi no Heart": "The Chrysalis Heart",
    "Lock On": "Lock On",
    "Metsuki Warui Ko Kawaii Ko": "Metsuki Warui",
    "The Gamer": "The Gamer",
    "Sharp": "Um colega de classe de olhar perspicaz",
    "naoto_san": "Don't bully me naoto-san",
    "23_4": "23:4",
}
MENCAO_TODAS_OBRAS = "todas as obras"
CORES_OBRAS = {
    "please don't die": 0x4DBD4A,
    "Sanagi no Heart": 0xE62371,
    "Lock On": 0x737373,
    "Metsuki Warui Ko Kawaii Ko": 0xFFFFFF,
    "The Gamer": 0x312564,
    "naoto_san": 0x4287f5,
    "sharp": 0xdf8f46,
    "23:4": 0x000000
}
COR_PADRAO_EMBED = 0x000000

MANGAS_PATH = Path("mangas.json")
ESTADO_PATH = Path("ultimo_capitulo.json")
COMANDOS_PROCESSADOS = set()
# Quantos capitulos o bot busca em cada fonte a cada verificacao.
LIMITE_CAPITULOS_RECENTES = 5
# Quantos lancamentos a API da scan pode devolver por pagina.
LIMITE_LANCAMENTOS_SCAN = 20
# Chave global usada para guardar a ultima checagem do endpoint da scan.
ESTADO_SCAN_KEY = "_nexus_scan"
# A checagem olha alguns dias para tras para conseguir detectar testes feitos alterando ultimo_capitulo.json.
HORAS_REVISAO_SCAN = 24 * 45
# Tempo de espera entre avisos automaticos para evitar rate limit do Discord.
PAUSA_ENTRE_ENVIOS = 3
# False = quando uma obra for vista pela primeira vez, o bot so salva o estado e nao avisa.
AVISAR_CAPITULO_INICIAL = False


# //////////////////////////////////////////////////////////////|INICIALIZACAO DO BOT|//////////////////////////////////
# Ativa leitura de mensagens e cria o bot com prefixo "#".
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)
# Evita duas verificacoes de capitulos rodando ao mesmo tempo.
verificacao_lock = asyncio.Lock()


# //////////////////////////////////////////////////////////////|LEITURA E SALVAMENTO DOS JSON|//////////////////////////////////
# Funcoes para abrir/salvar mangas.json e ultimo_capitulo.json.
def carregar_json(caminho, padrao):
    if not caminho.exists():
        return padrao

    with caminho.open("r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def salvar_json(caminho, dados):
    with caminho.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)


def carregar_mangas():
    return carregar_json(MANGAS_PATH, {})


def carregar_estado():
    return carregar_json(ESTADO_PATH, {})


def salvar_estado(estado):
    salvar_json(ESTADO_PATH, estado)


def agora_utc_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def data_inicial_scan_iso():
    data = datetime.now(timezone.utc) - timedelta(hours=HORAS_REVISAO_SCAN)
    return data.isoformat().replace("+00:00", "Z")


def ler_data_iso(data):
    if not data:
        return None

    try:
        return datetime.fromisoformat(str(data).replace("Z", "+00:00"))
    except ValueError:
        return None


def data_mais_antiga_iso(*datas):
    datas_validas = [data for data in datas if ler_data_iso(data)]
    if not datas_validas:
        return data_inicial_scan_iso()

    return min(datas_validas, key=ler_data_iso)


def normalizar_numero_capitulo(numero):
    if numero is None:
        return None

    texto = str(numero).strip()
    if texto.isdigit():
        return str(int(texto))

    return texto


def headers_nexus():
    headers = {
        "Accept": "application/json",
        "X-Skip-Encrypt": "true",
    }

    if NEXUS_API_TOKEN:
        headers["Authorization"] = f"Bearer {NEXUS_API_TOKEN}"
        headers["X-API-Key"] = NEXUS_API_TOKEN

    return headers


def buscar_emoji_link(nome):
    return EMOJIS_LINKS.get(nome, BOTAO_LINK_EMOJI)


def normalizar_chave_texto(texto):
    return str(texto or "").strip().casefold()


def cor_para_int(cor):
    if cor is None:
        return None

    if isinstance(cor, int):
        return cor

    texto = str(cor).strip()
    if texto.startswith("#"):
        texto = texto[1:]
    elif texto.lower().startswith("0x"):
        texto = texto[2:]

    try:
        return int(texto, 16)
    except ValueError:
        return None


def cor_embed_obra(manga):
    cor_configurada = cor_para_int(manga.get("cor") or manga.get("color"))
    if cor_configurada is not None:
        return cor_configurada

    cores_normalizadas = {
        normalizar_chave_texto(nome): cor for nome, cor in CORES_OBRAS.items()
    }
    chaves_obra = [
        manga.get("nome"),
        manga.get("nexus_slug"),
    ]

    for chave in chaves_obra:
        cor = cores_normalizadas.get(normalizar_chave_texto(chave))
        if cor is not None:
            return cor

    return COR_PADRAO_EMBED


def criar_embed_help():
    mangas = carregar_mangas()
    ids_obras = ", ".join(f"`{manga_id}`" for manga_id in sorted(mangas))
    if not ids_obras:
        ids_obras = "nenhuma obra cadastrada"

    embed = discord.Embed(
        title="Central de comandos",
        description=(
            "Comandos do bot de avisos da Alzheimer Scan.\n\n"
            "**Obras cadastradas**\n"
            f"{ids_obras}"
        ),
        color=0xDF8F46,
    )
    embed.add_field(
        name="#manga <obra>",
        value="Mostra a ficha da obra cadastrada.\nExemplo: `#manga gamer`",
        inline=False,
    )
    embed.add_field(
        name="#verificar",
        value="Forca uma checagem na Nexus e envia avisos se tiver capitulo novo.",
        inline=False,
    )
    embed.add_field(
        name="#testrecap <obra>",
        value="Simula capitulo novo daquela obra e roda a checagem logo depois.",
        inline=False,
    )
    embed.add_field(
        name="#forcar_aviso <obra>",
        value="Envia o aviso do ultimo capitulo da obra no canal configurado.",
        inline=False,
    )
    embed.add_field(
        name="#testar_mangadex <obra>",
        value="Mostra o ultimo capitulo da Nexus no chat atual, sem usar o canal de avisos.",
        inline=False,
    )
    embed.set_footer(text="Alzheimer Scan")
    return embed


# //////////////////////////////////////////////////////////////|BOTOES DE LINKS|//////////////////////////////////
# Cria os botoes do embed usando os links cadastrados em mangas.json.
def criar_view_links(links, usar_emojis=True):
    view = View()

    for nome, link in links.items():
        if not link:
            continue

        emoji = buscar_emoji_link(nome) if usar_emojis else None
        botao = Button(
            label=None if emoji else nome,
            emoji=emoji,
            url=link,
            style=discord.ButtonStyle.link,
        )
        view.add_item(botao)

    return view


# //////////////////////////////////////////////////////////////|EMBED DO COMANDO #MANGA|//////////////////////////////////
# Monta o embed principal da obra com nome, descricao, capa e botoes.
def criar_embed_e_botoes(manga_id):
    mangas = carregar_mangas()
    manga = mangas.get(manga_id)

    if not manga:
        return None, None

    embed = discord.Embed(
        title=manga["nome"],
        description=manga.get("descricao"),
        color=cor_embed_obra(manga),
    )

    if manga.get("capa"):
        embed.set_image(url=manga["capa"])

    view = criar_view_links(manga.get("links", {}))

    return embed, view


# //////////////////////////////////////////////////////////////|BUSCA DE CAPITULOS NA NEXUS|//////////////////////////////////
# Todas as obras buscam capitulos pela Nexus. O slug vem de nexus_slug ou do link "Nexus" em mangas.json.
async def buscar_capitulos_recentes(session, manga):
    slug = manga.get("nexus_slug")
    if not slug:
        link_nexus = manga.get("links", {}).get("Nexus", "")
        match = re.search(r"nexustoons\.com/manga/([^/?#]+)", link_nexus)
        slug = match.group(1) if match else None

    if not slug:
        return None

    url = f"{NEXUS_BASE}/api/manga/{slug}"
    async with session.get(url, headers=headers_nexus()) as response:
        response.raise_for_status()
        dados = await response.json()

    if "d" in dados and "v" in dados:
        raise aiohttp.ClientResponseError(
            response.request_info,
            response.history,
            status=response.status,
            message="A Nexus retornou dados criptografados. Configure NEXUS_API_TOKEN no .env.",
            headers=response.headers,
        )

    capitulos = dados.get("chapters", [])[:LIMITE_CAPITULOS_RECENTES]
    for capitulo in capitulos:
        capitulo["source"] = "nexus"
        capitulo["manga_slug"] = dados.get("slug") or slug

    return filtrar_capitulos_repetidos(capitulos)


async def buscar_detalhes_manga_nexus(session, slug):
    if not slug:
        return {}

    async with session.get(
        f"{NEXUS_BASE}/api/manga/{slug}",
        headers=headers_nexus(),
    ) as response:
        response.raise_for_status()
        dados = await response.json(content_type=None)

    if "d" in dados and "v" in dados:
        return {}

    return dados


# //////////////////////////////////////////////////////////////|BUSCA DE LANCAMENTOS DA SCAN NA NEXUS|//////////////////////////////////
# Busca todos os capitulos novos da scan desde a ultima checagem usando uma unica chamada por pagina.
async def buscar_lancamentos_scan(session, desde):
    lancamentos = []
    cursor = None

    while True:
        params = {
            "since": desde,
            "limit": str(LIMITE_LANCAMENTOS_SCAN),
        }
        if cursor:
            params["cursor"] = cursor

        async with session.get(
            f"{NEXUS_BASE}/api/v1/scan/chapters",
            headers=headers_nexus(),
            params=params,
        ) as response:
            response.raise_for_status()
            dados = await response.json(content_type=None)

        lancamentos.extend(dados.get("data", []))

        cursor = dados.get("nextCursor")
        if not dados.get("hasMore") or not cursor:
            break

    return lancamentos


def capitulo_da_scan(lancamento):
    numero = normalizar_numero_capitulo(lancamento.get("chapterNumber"))
    return {
        "id": lancamento.get("chapterId"),
        "number": numero,
        "title": lancamento.get("title"),
        "published_at": lancamento.get("publishedAt"),
        "manga_slug": lancamento.get("mangaSlug"),
        "manga_title": lancamento.get("mangaTitle"),
        "read_url": lancamento.get("readUrl"),
        "manga_url": lancamento.get("mangaUrl"),
        "source": "nexus",
    }


def mapa_mangas_por_slug(mangas):
    mapa = {}

    for manga_id, manga in mangas.items():
        slug = manga.get("nexus_slug")
        if not slug:
            link_nexus = manga.get("links", {}).get("Nexus", "")
            match = re.search(r"nexustoons\.com/manga/([^/?#]+)", link_nexus)
            slug = match.group(1) if match else None

        if slug:
            mapa[slug] = (manga_id, manga)

    return mapa


def manga_id_auto(slug, mangas):
    manga_id_base = re.sub(r"[^a-z0-9_]+", "_", slug.lower()).strip("_")
    manga_id = manga_id_base or f"obra_{len(mangas) + 1}"
    contador = 2

    while manga_id in mangas:
        manga_id = f"{manga_id_base}_{contador}"
        contador += 1

    return manga_id


def criar_manga_da_scan(lancamento, detalhes=None):
    detalhes = detalhes or {}
    titulo = (
        detalhes.get("title")
        or lancamento.get("mangaTitle")
        or lancamento.get("mangaSlug")
        or "Obra sem nome"
    )
    slug = lancamento.get("mangaSlug")
    manga_url = (
        lancamento.get("mangaUrl")
        or detalhes.get("rawUrl")
        or f"{NEXUS_BASE}/manga/{slug}"
    )

    return {
        "nome": titulo,
        "descricao": detalhes.get("description") or "",
        "capa": detalhes.get("coverImage") or lancamento.get("mangaCover"),
        "fonte_capitulos": "nexus",
        "nexus_slug": slug,
        "auto_cadastrado": True,
        "mencoes": ["todas"],
        "idiomas": ["pt-br"],
        "links": {
            "Nexus": manga_url,
        },
    }


# //////////////////////////////////////////////////////////////|IDENTIFICACAO DE CAPITULO REPETIDO|//////////////////////////////////
# Cria uma chave tipo "volume:capitulo" para evitar avisar upload duplicado do mesmo capitulo.
def chave_capitulo(capitulo):
    if capitulo.get("source") == "nexus":
        numero = normalizar_numero_capitulo(capitulo.get("number"))
        return f"nexus:{numero or capitulo.get('id')}"

    atributos = capitulo.get("attributes", {})
    volume = atributos.get("volume") or ""
    numero = atributos.get("chapter")

    if numero:
        return f"{volume}:{numero}"

    titulo = (atributos.get("title") or "").strip().lower()
    return titulo or capitulo.get("id")


def filtrar_capitulos_repetidos(capitulos):
    capitulos_unicos = []
    vistos = set()

    for capitulo in capitulos:
        chave = chave_capitulo(capitulo)
        if chave in vistos:
            continue

        vistos.add(chave)
        capitulos_unicos.append(capitulo)

    return capitulos_unicos


# //////////////////////////////////////////////////////////////|FORMATACAO DE CAPITULOS|//////////////////////////////////
# Transforma os dados da Nexus em textos legiveis para o subtitulo do aviso.
def numero_capitulo(capitulo):
    if capitulo.get("source") == "nexus":
        return normalizar_numero_capitulo(capitulo.get("number")) or "novo"

    atributos = capitulo.get("attributes", {})
    return atributos.get("chapter") or "novo"


def texto_numeros_capitulos(capitulos):
    numeros = [str(numero_capitulo(capitulo)) for capitulo in capitulos]

    if len(numeros) == 1:
        return numeros[0]

    if len(numeros) == 2:
        return f"{numeros[0]} e {numeros[1]}"

    return f"{', '.join(numeros[:-1])} e {numeros[-1]}"


def chamada_capitulos(capitulos):
    numeros = texto_numeros_capitulos(capitulos)
    palavra_capitulo = "CAPITULO" if len(capitulos) == 1 else "CAPITULOS"
    return (
        "\n🚨  NOVO\n"
        f"**{palavra_capitulo} {numeros}**\n"
        "veio como um Déjà vu repentino na memória\n" 
        "Já puxa a cadeira e vem ler antes que a memória falhe!\n\n"
        "Caso queira receber notificações de novos capítulos, entre em <id:customize> e pegue os cargos das obras que quer acompanhar.\n\n"
        "Boa Leitura!"
    )


def texto_parceria(manga):
    parceria = manga.get("parceria")
    if not parceria:
        return None

    if isinstance(parceria, list):
        parceria = ", ".join(str(nome) for nome in parceria if nome)

    return str(parceria) if parceria else None


# //////////////////////////////////////////////////////////////|EMBED DE AVISO DE CAPITULO|//////////////////////////////////
# Monta o embed enviado quando sai capitulo novo, sem link no titulo.
def descricao_capitulos(manga, capitulos):
    linhas = [f"# {manga['nome']}"]
    parceria = texto_parceria(manga)

    if parceria:
        linhas.append(f"parceria: {parceria}")

    linhas.append(chamada_capitulos(capitulos))
    return "\n".join(linhas)


def criar_embed_capitulos(manga, capitulos):
    embed = discord.Embed(
        description=descricao_capitulos(manga, capitulos),
        color=cor_embed_obra(manga),
    )

    if manga.get("capa"):
        embed.set_image(url=manga["capa"])

    embed.set_footer(text="Alzheimer Scan")

    return embed


def link_leitura_nexus(capitulo):
    if capitulo.get("read_url"):
        return capitulo["read_url"]

    slug = capitulo.get("manga_slug")
    chapter_id = capitulo.get("id")

    if slug and chapter_id:
        return f"{NEXUS_BASE}/ler/{slug}/{chapter_id}"

    return None


def links_aviso_capitulo(manga, capitulos):
    links = dict(manga.get("links", {}))
    return links


# //////////////////////////////////////////////////////////////|ENVIO DE MENSAGENS COM EMBED E BOTOES|//////////////////////////////////
# Envia mensagem com embed/botoes. Se um emoji estiver invalido, tenta de novo usando texto nos botoes.
async def enviar_com_links(
    destino,
    embed,
    links,
    content=None,
    pausar=False,
    allowed_mentions=None,
):
    view = criar_view_links(links)

    try:
        mensagem = await destino.send(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=allowed_mentions,
        )
    except discord.HTTPException as erro:
        if "Invalid emoji" not in str(erro):
            raise

        mensagem = await destino.send(
            content=content,
            embed=embed,
            view=criar_view_links(links, usar_emojis=False),
            allowed_mentions=allowed_mentions,
        )

    if pausar:
        await asyncio.sleep(PAUSA_ENTRE_ENVIOS)

    return mensagem


# //////////////////////////////////////////////////////////////|MENCOES DAS OBRAS|//////////////////////////////////
# Controla quais cargos serao marcados no aviso. Configure em mangas.json no campo "mencoes".
def buscar_mencao_cargo(destino, nome_cargo):
    guild = getattr(destino, "guild", None)

    if guild:
        for role in guild.roles:
            if role.name.casefold() == nome_cargo.casefold():
                return role.mention

    return f"@{nome_cargo}"


def tipos_mencao_obra(manga):
    mencoes = manga.get("mencoes", ["obra", "todas"])

    if isinstance(mencoes, str):
        mencoes = [mencoes]

    return mencoes


def mencoes_aviso(destino, manga):
    nome_cargo_obra = MENCOES_OBRAS.get(manga["nome"], manga["nome"])
    cargos = []

    for tipo_mencao in tipos_mencao_obra(manga):
        if tipo_mencao == "obra":
            cargos.append(nome_cargo_obra)
        elif tipo_mencao == "todas":
            cargos.append(MENCAO_TODAS_OBRAS)
        elif tipo_mencao:
            cargos.append(str(tipo_mencao))

    linha_mencoes = " ".join(buscar_mencao_cargo(destino, cargo) for cargo in cargos)
    if not linha_mencoes:
        return "Achou que a gente tinha esquecido de vocês?"

    return f"{linha_mencoes}\nAchou que a gente tinha esquecido de vocês?"


# //////////////////////////////////////////////////////////////|AVISO AUTOMATICO NO CANAL CONFIGURADO|//////////////////////////////////
# Envia o aviso de capitulo novo no canal do DISCORD_CHANNEL_ID, com @Nome da obra fora do embed.
async def enviar_aviso_capitulo(manga, capitulos):
    if not CANAL_AVISOS_ID:
        print("DISCORD_CHANNEL_ID nao foi configurado no .env")
        return

    if isinstance(capitulos, dict):
        capitulos = [capitulos]

    canal = bot.get_channel(int(CANAL_AVISOS_ID))
    if canal is None:
        canal = await bot.fetch_channel(int(CANAL_AVISOS_ID))

    await enviar_com_links(
        canal,
        criar_embed_capitulos(manga, capitulos),
        links_aviso_capitulo(manga, capitulos),
        content=mencoes_aviso(canal, manga),
        pausar=True,
        allowed_mentions=discord.AllowedMentions(
            roles=True,
            users=False,
            everyone=False,
        ),
    )
    print(f"Aviso enviado em {canal} para {manga['nome']}.")


# //////////////////////////////////////////////////////////////|LOOP AUTOMATICO DE VERIFICACAO|//////////////////////////////////
# Roda a cada 1 minuto. A trava impede uma verificacao de comecar enquanto outra ainda esta rodando.
@tasks.loop(minutes=1)
async def verificar_capitulos_novos():
    if verificacao_lock.locked():
        print("Verificacao ja em andamento; pulando esta rodada.")
        return

    async with verificacao_lock:
        await executar_verificacao_capitulos()


# //////////////////////////////////////////////////////////////|COMPARACAO ENTRE CAPITULOS NOVOS E SALVOS|//////////////////////////////////
# Busca lancamentos novos da scan na Nexus e avisa so as obras cadastradas em mangas.json.
async def executar_verificacao_capitulos():
    mangas = carregar_mangas()
    estado = carregar_estado()
    estado_scan = estado.get(ESTADO_SCAN_KEY, {})
    desde = data_mais_antiga_iso(
        estado_scan.get("last_checked_at"),
        data_inicial_scan_iso(),
    )
    mapa_slugs = mapa_mangas_por_slug(mangas)
    capitulos_por_manga = {}
    mudou_estado = False
    mudou_mangas = False
    inicio_checagem = agora_utc_iso()
    print(f"Buscando lancamentos da Nexus desde {desde}.")

    async with aiohttp.ClientSession(
        headers={"User-Agent": "Discord manga update bot"}
    ) as session:
        try:
            lancamentos = await buscar_lancamentos_scan(session, desde)
        except aiohttp.ClientError as erro:
            print(f"Erro ao buscar lancamentos da scan na Nexus: {erro}")
            return

        for lancamento in lancamentos:
            slug = lancamento.get("mangaSlug")
            if not slug:
                continue

            manga_encontrado = mapa_slugs.get(slug)
            if not manga_encontrado:
                manga_id = manga_id_auto(slug, mangas)
                try:
                    detalhes = await buscar_detalhes_manga_nexus(session, slug)
                except aiohttp.ClientError as erro:
                    print(f"Erro ao buscar detalhes de {slug} na Nexus: {erro}")
                    detalhes = {}

                manga = criar_manga_da_scan(lancamento, detalhes)
                mangas[manga_id] = manga
                mapa_slugs[slug] = (manga_id, manga)
                manga_encontrado = (manga_id, manga)
                mudou_mangas = True
                print(f"Obra nova cadastrada no mangas.json: {manga_id} ({manga['nome']})")

            manga_id, manga = manga_encontrado
            capitulo = capitulo_da_scan(lancamento)
            capitulos_por_manga.setdefault(manga_id, {"manga": manga, "capitulos": []})
            capitulos_por_manga[manga_id]["capitulos"].append(capitulo)

        for manga_id, dados in capitulos_por_manga.items():
            manga = dados["manga"]
            capitulos = filtrar_capitulos_repetidos(dados["capitulos"])
            dados_salvos = estado.get(manga_id, {})
            ultimo_salvo = dados_salvos.get("chapter_id")
            ultima_chave_salva = dados_salvos.get("chapter_key")

            if not capitulos:
                continue

            capitulo_atual = capitulos[0].get("id")
            chave_atual = chave_capitulo(capitulos[0])
            estado_atualizado = {
                "chapter_id": capitulo_atual,
                "chapter_key": chave_atual,
                "source": "nexus",
                "published_at": capitulos[0].get("published_at"),
            }

            if ultimo_salvo is None:
                if AVISAR_CAPITULO_INICIAL:
                    await enviar_aviso_capitulo(manga, list(reversed(capitulos)))

                estado[manga_id] = estado_atualizado
                mudou_estado = True
                continue

            novos_capitulos = []
            for capitulo in capitulos:
                chave = chave_capitulo(capitulo)
                if chave == ultima_chave_salva or (
                    not ultima_chave_salva and capitulo.get("id") == ultimo_salvo
                ):
                    break
                novos_capitulos.append(capitulo)

            if novos_capitulos:
                print(
                    f"Capitulo novo encontrado em {manga_id}: "
                    f"{ultima_chave_salva or ultimo_salvo} -> {chave_atual}"
                )
                await enviar_aviso_capitulo(manga, list(reversed(novos_capitulos)))

                estado[manga_id] = estado_atualizado
                mudou_estado = True

    estado[ESTADO_SCAN_KEY] = {"last_checked_at": inicio_checagem}
    mudou_estado = True

    if mudou_estado:
        salvar_estado(estado)

    if mudou_mangas:
        salvar_json(MANGAS_PATH, mangas)


# //////////////////////////////////////////////////////////////|ESPERA O BOT FICAR PRONTO|//////////////////////////////////
# Garante que o loop automatico so comece depois que o bot estiver conectado.
@verificar_capitulos_novos.before_loop
async def antes_de_verificar_capitulos():
    await bot.wait_until_ready()


# //////////////////////////////////////////////////////////////|EVENTO AO LIGAR O BOT|//////////////////////////////////
# Quando o bot loga, mostra no terminal e inicia a verificacao automatica.
@bot.event
async def on_ready():
    print(f"Logado como {bot.user}")

    if not verificar_capitulos_novos.is_running():
        verificar_capitulos_novos.start()


# //////////////////////////////////////////////////////////////|COMANDO #MANGA|//////////////////////////////////
# Uso: #manga nome
# Mostra o embed da obra cadastrada em mangas.json.
@bot.command(name="help")
async def help_command(ctx):
    await ctx.send(embed=criar_embed_help())


@bot.command()
async def manga(ctx, nome):
    if ctx.message.id in COMANDOS_PROCESSADOS:
        return

    COMANDOS_PROCESSADOS.add(ctx.message.id)

    embed, view = criar_embed_e_botoes(nome.lower())

    if embed:
        manga_data = carregar_mangas()[nome.lower()]
        await enviar_com_links(ctx, embed, manga_data.get("links", {}))
    else:
        await ctx.send("esse manga nao ta registrado ainda")


# //////////////////////////////////////////////////////////////|COMANDO #VERIFICAR|//////////////////////////////////
# Uso: #verificar
# Forca uma verificacao manual de capitulos novos.
@bot.command()
async def verificar(ctx):
    if verificacao_lock.locked():
        await ctx.send("ja tem uma verificacao em andamento, tenta de novo em alguns segundos.")
        return

    async with verificacao_lock:
        await executar_verificacao_capitulos()

    await ctx.send("verificacao feita.")


# //////////////////////////////////////////////////////////////|COMANDO #TESTAR_NEXUS|//////////////////////////////////
# Uso: #testar_mangadex nome
# Busca o ultimo capitulo na Nexus e mostra no chat atual, sem mandar no canal de avisos.
@bot.command()
async def testar_mangadex(ctx, nome):
    mangas = carregar_mangas()
    manga = mangas.get(nome.lower())

    if not manga:
        await ctx.send("esse manga nao ta registrado ainda")
        return

    async with aiohttp.ClientSession(
        headers={"User-Agent": "Discord manga update bot"}
    ) as session:
        capitulos = await buscar_capitulos_recentes(session, manga)

    if not capitulos:
        await ctx.send("nao achei capitulos desse manga na fonte configurada")
        return

    await enviar_com_links(
        ctx,
        criar_embed_capitulos(manga, [capitulos[0]]),
        manga.get("links", {}),
    )


# //////////////////////////////////////////////////////////////|COMANDO #TESTRECAP|//////////////////////////////////
# Uso: #testrecap nome
# Randomiza o ultimo capitulo salvo da obra e roda a verificacao logo depois.
@bot.command()
async def testrecap(ctx, nome):
    mangas = carregar_mangas()
    manga_id = nome.lower()
    manga = mangas.get(manga_id)

    if not manga:
        await ctx.send("esse manga nao ta registrado ainda")
        return

    estado = carregar_estado()
    id_teste = str(uuid.uuid4())
    estado[manga_id] = {
        "chapter_id": id_teste,
        "chapter_key": f"nexus:testrecap:{id_teste}",
        "source": "nexus",
    }
    estado[ESTADO_SCAN_KEY] = {"last_checked_at": "2026-05-01T00:00:00Z"}
    salvar_estado(estado)

    if verificacao_lock.locked():
        await ctx.send("ja tem uma verificacao em andamento, tenta de novo em alguns segundos.")
        return

    async with verificacao_lock:
        await executar_verificacao_capitulos()


# //////////////////////////////////////////////////////////////|COMANDO #FORCAR_AVISO|//////////////////////////////////
# Uso: #forcar_aviso nome
# Manda o aviso do ultimo capitulo diretamente no canal configurado.
@bot.command()
async def forcar_aviso(ctx, nome):
    mangas = carregar_mangas()
    manga = mangas.get(nome.lower())

    if not manga:
        await ctx.send("esse manga nao ta registrado ainda")
        return

    async with aiohttp.ClientSession(
        headers={"User-Agent": "Discord manga update bot"}
    ) as session:
        capitulos = await buscar_capitulos_recentes(session, manga)

    if not capitulos:
        await ctx.send("nao achei capitulos desse manga na fonte configurada")
        return

    await enviar_aviso_capitulo(manga, capitulos[0])
    await ctx.send("aviso enviado no canal configurado.")


# //////////////////////////////////////////////////////////////|INICIAR BOT|//////////////////////////////////
# Liga o bot usando o token do .env.
bot.run(TOKEN)
