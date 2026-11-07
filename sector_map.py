"""
sector_map.py — Arka rotation taxonomy (v1)

Why this file exists
--------------------
The NSE index files (ind_nifty500list.csv …) carry only the 22-sector level of NSE's classification in their
"Industry" column. That lumps PSU banks, private banks, NBFCs, insurers and exchanges into one "Financial
Services" bucket, and transformers, cables, defence and pumps into one "Capital Goods" bucket. Money rarely
rotates at that level: it rotates between groups like "PSU Banks" and "Capital Markets". This file assigns
every stock to one of ~50 tradeable rotation groups.

How a stock gets its group (first match wins)
  1. data/sector_map.csv with a "group" column            your manual override
  2. GROUPS below                                          curated list for NIFTY 500 + MICROCAP 250
  3. RULES on NSE Basic Industry / Industry text           when data/sector_map.csv has those columns
                                                           (tools/build_sector_map.py downloads them)
  4. company-name keywords + NSE broad sector              last resort for anything new

Edit GROUPS freely: move a symbol by cutting it from one string and pasting it into another.
"""
from __future__ import annotations

import re

# group -> (family, symbols). A family is the broad bucket a small group folds into (fewer than 3 stocks).
GROUPS = {
    # ── Financials ──
    "Private Banks": ("Financials", "HDFCBANK ICICIBANK AXISBANK KOTAKBANK INDUSINDBK IDFCFIRSTB FEDERALBNK BANDHANBNK "
                                    "RBLBANK YESBANK AUBANK CUB KARURVYSYA J&KBANK CSBBANK DCBBANK KTKBANK SOUTHBANK TMB"),
    "PSU Banks": ("Financials", "SBIN BANKBARODA PNB CANBK UNIONBANK BANKINDIA INDIANB MAHABANK CENTRALBK IOB UCOBANK IDBI PSB"),
    "Small Finance Banks": ("Financials", "EQUITASBNK UJJIVANSFB JSFB CAPITALSFB SURYODAY ESAFSFB UTKARSHBNK"),
    "NBFC": ("Financials", "BAJFINANCE BAJAJFINSV CHOLAFIN SHRIRAMFIN MUTHOOTFIN MANAPPURAM M&MFIN LTF ABCAPITAL JIOFIN "
                           "SBICARD SUNDARMFIN POONAWALLA PIRAMALFIN TATACAP HDBFS IIFL CGCL FIVESTAR CREDITACC FEDFINA "
                           "NORTHARC PAISALO SBFC EDELWEISS SPANDANA FUSION"),
    "Housing Finance": ("Financials", "LICHSGFIN PNBHOUSING BAJAJHFL AADHARHFC AAVAS APTUS HOMEFIRST CANFINHOME SAMMAANCAP REPCOHOME"),
    "PSU Financiers": ("Financials", "PFC RECLTD IRFC IREDA HUDCO IFCI"),
    "Insurance": ("Financials", "LICI SBILIFE HDFCLIFE ICICILIFE ICICIPRULI ICICIGI GICRE NIACL STARHEALTH MFSL CANHLIFE NIVABUPA GODIGIT"),
    "Capital Markets": ("Financials", "BSE MCX CDSL CAMS KFINTECH HDFCAMC NAM-INDIA ICICIAMC ABSLAMC UTIAMC CRAMC ANGELONE "
                                      "MOTILALOFS 360ONE ANANDRATHI NUVAMA IEX CRISIL ICRA CARERATING JMFINANCIL CHOICEIN GROWW "
                                      "IIFLCAPS SHAREINDIA RELIGARE PRUDENT"),
    "Fintech": ("Financials", "PAYTM POLICYBZR PINELABS KISSHT ZAGGLE MOBIKWIK"),
    "Investment & Holding Cos": ("Financials", "BAJAJHLDNG TATAINVEST CHOLAHLDNG AIIL GODREJIND KICL"),
    # ── Healthcare ──
    "Pharmaceuticals": ("Healthcare", "SUNPHARMA DRREDDY CIPLA DIVISLAB LUPIN ZYDUSLIFE AUROPHARMA TORNTPHARM ALKEM MANKIND "
                                      "ABBOTINDIA GLAXO GLENMARK IPCALAB AJANTPHARM BIOCON LAURUSLABS NATCOPHARM GRANULES GLAND "
                                      "EMCURE JBCHEPHARM CAPLIPOINT CONCORDBIO NEULANDLAB PPLPHARMA ONESOURCE WOCKPHARMA SYNGENE "
                                      "SAILIFE COHANCE ANTHEM ACUTAAS RUBICON AARTIPHARM ADVENZYMES AKUMS BLUEJET ERIS JUBLPHARMA "
                                      "MARKSANS PFIZER PGHL SENORES SHILPAMED SPARC STAR SUPRIYA SUVEN VIYASH ALEMBICLTD APLLTD"),
    "Healthcare Services": ("Healthcare", "APOLLOHOSP MAXHEALTH FORTIS MEDANTA NH ASTERDM KIMS RAINBOW LALPATHLAB METROPOLIS "
                                          "VIJAYA THYROCARE HCG YATHARTH PARKHOSPS NEPHROPLUS JSLL ENTERO POLYMED"),
    # ── Technology & telecom ──
    "IT Services": ("Technology", "TCS INFY HCLTECH WIPRO TECHM LTM LTIM PERSISTENT COFORGE MPHASIS HEXT KPITTECH TATAELXSI "
                                  "LTTS CYIENT TATATECH BSOFT BBOX ZENSARTECH SONATSOFTW FRACTAL IKS HAPPSTMNDS MASTEK "
                                  "DATAMATICS LATENTVIEW INDGN"),
    "Software Products": ("Technology", "OFSS INTELLECT NEWGEN TANLA ROUTE AFFLE MAPMYINDIA RATEGAIN AURIONPRO AMAGI CAPILLARY E2E"),
    "Internet & E-commerce": ("Technology", "ETERNAL SWIGGY NAUKRI NYKAA INDIAMART JUSTDIAL CARTRADE IXIGO MEESHO URBANCO "
                                            "FIRSTCRY PWL EASEMYTRIP"),
    "Electronics Manufacturing": ("Technology", "DIXON KAYNES SYRMA AMBER PGEL AVALON CENTUM CPPLUS NETWEB EBGNG"),
    "Telecom": ("Technology", "BHARTIARTL IDEA BHARTIHEXA TATACOMM INDUSTOWER TTML HFCL STLTECH TEJASNET ITI"),
    # ── Auto ──
    "Automobiles": ("Auto", "MARUTI M&M TMPV TMCV TATAMOTORS BAJAJ-AUTO HEROMOTOCO EICHERMOT TVSMOTOR ASHOKLEY HYUNDAI "
                            "ESCORTS FORCEMOT OLAELEC OLECTRA ATHERENERG SMLMAH"),
    "Auto Components": ("Auto", "BOSCHLTD MOTHERSON UNOMINDA SONACOMS BHARATFORG SCHAEFFLER TIINDIA ENDURANCE EXIDEIND ARE&M "
                                "MSUMI MINDACORP CRAFTSMAN SANSERA RKFORGE CIEINDIA GABRIEL ZFCVINDIA TENNIND JBMA ASAHIINDIA "
                                "SHRIPISTON BELRISE BANCOINDIA FIEMIND HAPPYFORGE JAMNAAUTO LUMAXTECH LUMAXIND PRICOLLTD SJS "
                                "SEDEMAC SUBROS SUNDRMFAST"),
    "Tyres": ("Auto", "MRF APOLLOTYRE BALKRISIND CEATLTD JKTYRE TVSSRICHAK"),
    # ── Industrials ──
    "Electrical Equipment": ("Industrials", "ABB SIEMENS ENRIN CGPOWER POWERINDIA GVT&D BHEL SCHNEIDER TARIL TRITURBINE "
                                            "TDPOWERSYS VOLTAMP QPOWER ATLANTAELE SHILCTECH GENUSPOWER POWERICA SKIPPER"),
    "Cables & Wires": ("Industrials", "POLYCAB KEI FINCABLES RRKABEL APARINDS PRECWIRE DIACABS UNIVCABLES"),
    "Aerospace & Defence": ("Industrials", "HAL BEL BDL MAZDOCK GRSE COCHINSHIP DATAPATTNS ZENTEC PARAS ASTRAMICRO APOLLO "
                                           "MIDHANI SOLARINDS MTARTECH AZAD PTCIL BEML UNIMECH DYNAMATECH SIGMAADV AXISCADES AEQUS"),
    "Railways": ("Industrials", "RVNL IRCON RITES TITAGARH JWL TEXRAIL RAILTEL IRCTC HBLENGINE"),
    "Industrial Machinery": ("Industrials", "CUMMINSIND KIRLOSENG ELGIEQUIP KSB KIRLOSBROS ACE THERMAX PRAJIND ELECON TEGA "
                                            "GREAVESCOT KIRLPNU SHAKTIPUMP OSWALPUMPS JYOTICNC INOXINDIA HONAUT KRN LLOYDSENGG"),
    "Industrial Products": ("Industrials", "AIAENG TIMKEN GRINDWELL CARBORUNIV GRAPHITE HEGAM RHIM VESUVIUS ELECTCAST AEROFLEX "
                                           "BALUFORGE OMNI"),
    "Renewable Energy": ("Industrials", "ADANIGREEN ACMESOLAR NTPCGREEN SUZLON INOXWIND WAAREEENER PREMIERENE EMMVEE CLEANMAX "
                                        "KPIGREEN BORORENEW WEBELSOLAR VIKRAMSOLR WAAREERTL UTLSOLAR SWSOLAR"),
    # ── Construction & real estate ──
    "Construction & Engineering": ("Construction", "LT NCC KPIL KEC AFCONS NBCC ENGINERSIN IRB PNCINFRA KNRCON HGINFRA ASHOKA "
                                                   "DBL CEIGALL HCC POWERMECH WELENT TECHNOE CEMPRO TRANSRAILL"),
    "Cement": ("Construction", "ULTRACEMCO AMBUJACEM ACC SHREECEM DALBHARAT JKCEMENT RAMCOCEM INDIACEM NUVOCO JSWCEMENT "
                               "BIRLACORPN GRASIM JKLAKSHMI HEIDELBERG STARCEMENT"),
    "Building Products": ("Construction", "ASTRAL SUPREMEIND FINPIPE KAJARIACER CERA STYLAMIND RESPONIND CENTURYPLY GREENPLY "
                                          "PRINCEPIPE SOMANYCERA"),
    "Paints": ("Construction", "ASIANPAINT BERGEPAINT KANSAINER INDIGOPNTS AKZOINDIA"),
    "Realty": ("Construction", "DLF GODREJPROP LODHA OBEROIRLTY PRESTIGE PHOENIXLTD BRIGADE SOBHA SIGNATURE ANANTRAJ ABREL "
                               "PURVA SUNTECK TARC EMBDL DBREALTY LOTUSDEV MAHLIFE KOLTEPATIL"),
    # ── Metals & energy ──
    "Steel": ("Metals", "TATASTEEL JSWSTEEL JINDALSTEL SAIL JSL NSLNISP VISL GPIL SARDAEN SHYAMMETL LLOYDSME JAIBALAJI "
                        "JAYNECOIND SUNFLAG GALLANTT USHAMART APLAPOLLO RATNAMANI WELCORP JINDALSAW SGMART LLOYDSENT"),
    "Non-Ferrous & Mining": ("Metals", "HINDALCO NATIONALUM VEDL VAML HINDZINC HINDCOPPER NMDC GMDCLTD MOIL ADANIENT GRAVITA "
                                       "JAINREC CMRGREEN IMFA SANDUMA ASHAPURMIN"),
    "Refineries & Lubricants": ("Energy", "RELIANCE IOC BPCL HINDPETRO MRPL CHENNPETRO CASTROLIND GULFOILLUB"),
    "Oil & Gas Exploration": ("Energy", "ONGC OIL VOGL"),
    "Gas Distribution": ("Energy", "GAIL PETRONET IGL MGL ATGL AEGISLOG AEGISVOPAK GUJGASLTD GSPL"),
    "Coal": ("Energy", "COALINDIA BHARATCOAL CMPDI"),
    "Power": ("Energy", "NTPC POWERGRID TATAPOWER ADANIPOWER ADANIENSOL JSWENERGY NHPC SJVN NLCINDIA CESC TORNTPOWER JPPOWER "
                        "RPOWER RTNPOWER PTC GMRP&UI NAVA VEDPOWER"),
    "Water & Environment": ("Energy", "WABAG IONEXCHANG EIEL REFEX"),
    # ── Chemicals ──
    "Specialty Chemicals": ("Chemicals", "PIDILITIND SRF NAVINFLUOR FLUOROCHEM DEEPAKNTR AARTIIND ATUL CLEAN AETHER ANURAS "
                                         "PRIVISCL JUBLINGREA HSCL PCBL LINDEINDIA ALKYLAMINE BALAMINES NEOGEN LXCHEM SUDARSCHEM "
                                         "ELLEN SUDEEPPHRM VINATIORGA GALAXYSURF ROSSARI FINEORG"),
    "Commodity Chemicals": ("Chemicals", "TATACHEM GUJALKALI SPLPETRO ACI RAIN INDIAGLYCO SWANCORP GHCL DCMSHRIRAM"),
    "Fertilizers & Agrochemicals": ("Chemicals", "CHAMBLFERT COROMANDEL FACT PARADEEP RCF NFL GSFC GNFC DEEPAKFERT UPL PIIND "
                                                 "SUMICHEM RALLIS SHARDACROP MBAPL KSCL BAYERCROP DHANUKA"),
    # ── Consumer ──
    "FMCG": ("Consumer", "HINDUNILVR ITC NESTLEIND BRITANNIA DABUR MARICO GODREJCP COLPAL TATACONSUM EMAMILTD GILLETTE "
                         "PGHH JYOTHYLAB BAJAJCON HONASA DOMS CUPID ZYDUSWELL PATANJALI AWL VBL LTFOODS KRBL CCL HERITGFOOD "
                         "AVANTIFEED GODREJAGRO BECTORFOOD MANORAMA GOKULAGRO GAEL KWIL GODFRYPHLP VSTIND"),
    "Alcoholic Beverages": ("Consumer", "UBL UNITDSPR RADICO ABDL TI PICCADIL SULA GLOBUSSPR"),
    "Sugar": ("Consumer", "BALRAMCHIN EIDPARRY RENUKA TRIVENI DALMIASUG"),
    "Consumer Durables": ("Consumer", "HAVELLS VOLTAS BLUESTARCO CROMPTON WHIRLPOOL LGEINDIA VGUARD TTKPRESTIG IFBIND CELLO SFL "
                                      "WAKEFIT SURYAROSNI ORIENTELEC BAJAJELEC SYMPHONY"),
    "Footwear & Luggage": ("Consumer", "BATAINDIA RELAXO CAMPUS SAFARI VIPIND METROBRAND"),
    "Jewellery & Watches": ("Consumer", "TITAN KALYANKJIL SENCO PNGJL PCJEWELLER SKYGOLD GOLDIAM THANGAMAYL BLUESTONE VAIBHAVGBL "
                                        "TIMEX RAJESHEXPO"),
    "Retail": ("Consumer", "DMART TRENT VMM ABFRL ABLBL VMART V2RETAIL ARVINDFASN MANYAVAR MEDPLUS EMIL AVL LENSKART SHOPERSTOP"),
    "Textiles & Apparel": ("Consumer", "PAGEIND KPRMILL TRIDENT VTL WELSPUNLIV ARVIND GOKEX ICIL KITEX PDSL PGIL RAYMONDLSL"),
    "Hotels & Travel": ("Consumer", "INDHOTEL EIHOTEL ITCHOTELS LEMONTREE CHALET THELEELA SAMHI THOMASCOOK TBOTEK"),
    "Restaurants (QSR)": ("Consumer", "JUBLFOOD DEVYANI WESTLIFE SAPPHIRE RBA"),
    "Media & Entertainment": ("Consumer", "ZEEL SUNTV PVRINOX SAREGAMA TIPSMUSIC NAZARA NETWORK18 PFOCUS"),
    # ── Services ──
    "Logistics & Shipping": ("Services", "CONCOR DELHIVERY TVSSCS BLACKBUCK SHADOWFAX GESHIP SCI SHREEJISPG KMEW ALLCARGO VRLLOG"),
    "Airlines, Ports & Airports": ("Services", "INDIGO ADANIPORTS JSWINFRA GMRAIRPORT GPPL"),
    "Business Services": ("Services", "QUESS FSL ECLERX SAGILITY CMSINFO REDINGTON MMTC MSTCLTD IGIL BLS WEWORK TEAMLEASE"),
    "Packaging & Paper": ("Services", "EPL TIMETECHNO GRWRHITECH SHAILY JKPAPER"),
}

FAMILY = {g: fam for g, (fam, _) in GROUPS.items()}
_BY_SYMBOL = {}
for _g, (_fam, _syms) in GROUPS.items():
    for _s in _syms.split():
        _BY_SYMBOL.setdefault(_s.upper(), _g)

# Plain-English one-liners shown in the UI
ABOUT = {
    "Capital Markets": "exchanges, brokers, AMCs, depositories, registrars",
    "PSU Financiers": "PFC, REC, IRFC, IREDA, HUDCO: government-owned lenders",
    "Electronics Manufacturing": "EMS and electronics makers (Dixon, Kaynes, Syrma…)",
    "Railways": "rail PSUs, wagon makers, rail signalling",
    "Water & Environment": "water treatment and environmental engineering",
}

# NSE Basic Industry / Industry text -> group (used when data/sector_map.csv carries NSE classification)
RULES = [
    (r"public sector bank", "PSU Banks"), (r"small finance bank", "Small Finance Banks"),
    (r"private sector bank|other bank|foreign bank|^banks?$", "Private Banks"),
    (r"housing finance", "Housing Finance"), (r"insurance", "Insurance"),
    (r"stockbrok|exchange and data|depositor|clearing|asset management|wealth|rating|registrar|capital market", "Capital Markets"),
    (r"financial institution", "PSU Financiers"), (r"investment compan|holding compan", "Investment & Holding Cos"),
    (r"fintech|payment", "Fintech"), (r"non banking|nbfc|microfinance|financ", "NBFC"),
    (r"hospital|healthcare service|diagnostic|medical equipment|healthcare supplies", "Healthcare Services"),
    (r"pharma|biotech|drug|formulation", "Pharmaceuticals"),
    (r"e-retail|e-commerce|internet|online|digital platform", "Internet & E-commerce"),
    (r"software product", "Software Products"),
    (r"software|it enabled|computers|it services|consulting", "IT Services"),
    (r"electronic component|electronics manufactur|computer hardware", "Electronics Manufacturing"),
    (r"telecom", "Telecom"),
    (r"passenger car|utility vehicle|wheeler|commercial vehicle|tractor", "Automobiles"),
    (r"tyre", "Tyres"), (r"auto component|auto ancillar|batteries - automobile", "Auto Components"),
    (r"cable", "Cables & Wires"), (r"aerospace|defen[cs]e|ship ?build", "Aerospace & Defence"),
    (r"railway", "Railways"),
    (r"heavy electrical|electrical equipment|other electrical|transformer|switchgear", "Electrical Equipment"),
    (r"compressor|pump|diesel engine|industrial machinery|construction vehicle|machine tool", "Industrial Machinery"),
    (r"cement", "Cement"), (r"paint", "Paints"),
    (r"iron ?& ?steel|steel|sponge iron|pig iron", "Steel"),
    (r"ceramic|sanitary|plywood|laminate|granite|marble|pipes", "Building Products"),
    (r"residential|commercial project|real estate|reit|realty", "Realty"),
    (r"civil construction|engineering services|road assets|construction", "Construction & Engineering"),
    (r"alumin|copper|zinc|diversified metal|mining|mineral|ferro", "Non-Ferrous & Mining"),
    (r"refiner|lubricant|petroleum product", "Refineries & Lubricants"),
    (r"exploration|oil ?& ?gas production", "Oil & Gas Exploration"),
    (r"\bgas\b|lng|cng|png", "Gas Distribution"), (r"coal", "Coal"),
    (r"solar|wind|renewable", "Renewable Energy"), (r"power|electricity", "Power"),
    (r"water|waste|environment", "Water & Environment"),
    (r"fertili[sz]er|pesticide|agro ?chem|seed", "Fertilizers & Agrochemicals"),
    (r"commodity chem|petrochem|alkali|carbon black|soda ash", "Commodity Chemicals"),
    (r"chemical|industrial gas|explosive|dye|pigment", "Specialty Chemicals"),
    (r"brewer|distiller|alcohol|spirit", "Alcoholic Beverages"), (r"sugar", "Sugar"),
    (r"gems|jewel|watch", "Jewellery & Watches"), (r"footwear|luggage|leather", "Footwear & Luggage"),
    (r"household appliance|houseware|furniture|home furnish|consumer durable|lighting|consumer electronics", "Consumer Durables"),
    (r"hotel|resort|tour|travel", "Hotels & Travel"), (r"restaurant|quick service", "Restaurants (QSR)"),
    (r"retail|department store", "Retail"),
    (r"media|film|television|\btv\b|broadcast|entertainment|printing|publication|music", "Media & Entertainment"),
    (r"airline|airport|\bports?\b", "Airlines, Ports & Airports"),
    (r"logistic|shipping|courier|transport|warehous", "Logistics & Shipping"),
    (r"garment|apparel|textile|yarn|fabric|cotton", "Textiles & Apparel"),
    (r"paper|packaging|plastic product", "Packaging & Paper"),
    (r"cigarette|tobacco|personal care|packaged food|tea|coffee|edible oil|dairy|household product|fmcg|food|beverage|stationery",
     "FMCG"),
    (r"abrasive|bearing|casting|forging|industrial product|refractor|electrode", "Industrial Products"),
    (r"business process|bpo|kpo|commercial service|staffing|trading|distribut|education", "Business Services"),
]
_RULES = [(re.compile(p, re.I), g) for p, g in RULES]

# NSE 22-sector name (the "Industry" column of the index files) -> family + default group
_BROAD = {
    "financial services": ("Financials", "NBFC"), "healthcare": ("Healthcare", "Pharmaceuticals"),
    "information technology": ("Technology", "IT Services"), "telecommunication": ("Technology", "Telecom"),
    "automobile and auto components": ("Auto", "Auto Components"), "capital goods": ("Industrials", "Industrial Products"),
    "construction": ("Construction", "Construction & Engineering"), "construction materials": ("Construction", "Cement"),
    "realty": ("Construction", "Realty"), "metals & mining": ("Metals", "Non-Ferrous & Mining"),
    "oil gas & consumable fuels": ("Energy", "Refineries & Lubricants"), "power": ("Energy", "Power"),
    "utilities": ("Energy", "Water & Environment"), "chemicals": ("Chemicals", "Specialty Chemicals"),
    "fast moving consumer goods": ("Consumer", "FMCG"), "consumer durables": ("Consumer", "Consumer Durables"),
    "consumer services": ("Consumer", "Retail"), "textiles": ("Consumer", "Textiles & Apparel"),
    "media entertainment & publication": ("Consumer", "Media & Entertainment"), "services": ("Services", "Business Services"),
    "forest materials": ("Services", "Packaging & Paper"), "diversified": ("Financials", "Investment & Holding Cos"),
}
# (broad sector, company-name pattern, group) — refines the broad default using the company name
_NAME_HINTS = [
    ("financial services", r"small finance bank", "Small Finance Banks"),
    ("financial services", r"\bbank\b", "Private Banks"), ("financial services", r"housing|home fin", "Housing Finance"),
    ("financial services", r"insurance|assurance|\blife\b", "Insurance"),
    ("financial services", r"asset management|\bamc\b|securities|broking|capital services|exchange|depositor|wealth",
     "Capital Markets"),
    ("healthcare", r"hospital|health|diagnos|medicare|clinic", "Healthcare Services"),
    ("automobile and auto components", r"tyre|rubber", "Tyres"),
    ("capital goods", r"cable|wire", "Cables & Wires"),
    ("capital goods", r"electric|transformer|switchgear|power system", "Electrical Equipment"),
    ("capital goods", r"solar|wind|renewable|green", "Renewable Energy"),
    ("capital goods", r"defen[cs]e|aero|space|shipyard", "Aerospace & Defence"),
    ("capital goods", r"pump|compressor|engine|machine|cnc", "Industrial Machinery"),
    ("capital goods", r"steel|tube|pipe|ispat", "Steel"),
    ("construction materials", r"tile|ceramic|ply|laminate|pipe", "Building Products"),
    ("metals & mining", r"steel|ispat|iron", "Steel"),
    ("oil gas & consumable fuels", r"\bgas\b", "Gas Distribution"), ("oil gas & consumable fuels", r"coal", "Coal"),
    ("power", r"solar|green|renewable|wind", "Renewable Energy"),
    ("chemicals", r"fertili|agro|crop|seed|pesticide", "Fertilizers & Agrochemicals"),
    ("fast moving consumer goods", r"sugar", "Sugar"),
    ("fast moving consumer goods", r"brewer|distill|spirit|liquor|wine", "Alcoholic Beverages"),
    ("consumer durables", r"jewel|gold|diamond|watch", "Jewellery & Watches"),
    ("consumer durables", r"paint", "Paints"), ("consumer durables", r"footwear|shoe", "Footwear & Luggage"),
    ("consumer services", r"hotel|resort", "Hotels & Travel"), ("consumer services", r"food|restaurant", "Restaurants (QSR)"),
    ("services", r"logistic|shipping|transport|express|cargo", "Logistics & Shipping"),
    ("services", r"\bport|airport|aviation", "Airlines, Ports & Airports"),
]
_NAME_HINTS = [(b, re.compile(p, re.I), g) for b, p, g in _NAME_HINTS]


def family(group: str) -> str:
    if group in FAMILY:
        return FAMILY[group]
    for fam, _ in _BROAD.values():
        if group.startswith(fam):
            return fam
    return "Other"


def classify(symbol: str, company: str = "", nse_sector: str = "", basic: str = "", industry: str = "",
             group: str = "") -> str:
    """Rotation group for one stock (see the module docstring for the order)."""
    if group and str(group).strip() and str(group).strip().lower() != "nan":
        return str(group).strip()
    sym = str(symbol).upper().strip()
    if sym in _BY_SYMBOL:
        return _BY_SYMBOL[sym]
    for text in (basic, industry):
        text = str(text or "")
        if text and text.lower() != "nan":
            for rx, g in _RULES:
                if rx.search(text):
                    return g
    broad = str(nse_sector or "").strip().lower()
    name = str(company or "")
    for b, rx, g in _NAME_HINTS:
        if b == broad and rx.search(name):
            return g
    if broad in _BROAD:
        return _BROAD[broad][1]
    return "Unclassified"


def known_symbols() -> set:
    return set(_BY_SYMBOL)
