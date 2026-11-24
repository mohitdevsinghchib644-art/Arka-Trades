"""
mobile.py — Arka Trades phone layout (v4, for app.py v10.6: five sections in the bottom bar, pages as pills on top)

PHONE_CSS is injected when app.py decides the device is a phone (real screen width < 820 px, or forced in
ACCOUNT → Display). MOBILE_CSS holds a small safety subset that applies on narrow screens even before the width
is known. Selectors avoid Streamlit internals that change between versions: keyed containers (st-key-*),
data-testid hooks and plain roles.
"""

_SAFE = """
.block-container{padding-left:14px !important;padding-right:14px !important;}
input,textarea,select,[data-baseweb="select"] input{font-size:16px !important;}          /* no iOS zoom on focus */
[data-testid="stRadio"] [role="radiogroup"]{flex-wrap:nowrap !important;overflow-x:auto;scrollbar-width:none;padding-bottom:2px;}
[data-testid="stRadio"] [role="radiogroup"]::-webkit-scrollbar{display:none;}
[data-testid="stRadio"] [role="radiogroup"]>*{flex:0 0 auto !important;}
[data-testid="stRadio"] [role="radiogroup"] p{white-space:nowrap !important;}
.stTabs [data-baseweb="tab-list"]{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none;}
.stTabs [data-baseweb="tab"]{flex:0 0 auto;}
"""

_PHONE = _SAFE + """
.block-container{padding-top:0 !important;padding-bottom:calc(96px + env(safe-area-inset-bottom)) !important;}
[data-testid="stVerticalBlock"]{gap:.7rem;}

/* top app bar */
.ak-phdr{min-height:52px;padding:4px 0 0;}
.ak-phdr .ak-meta{min-height:52px;gap:10px;}
.ak-phdr .ak-av{width:30px;height:30px;}

/* page header: title only */
.ak-ph{padding:6px 0 2px;}
.ak-ph .t{font-size:20px;}
.ak-ph .d{display:none;}
.ak-ph .r{display:none;}

/* bottom tab bar */
div[class*="st-key-tabbar"]{position:fixed !important;left:0 !important;right:0 !important;bottom:0 !important;z-index:999990 !important;
  margin:0 !important;width:100% !important;gap:0 !important;justify-content:space-around !important;
  padding:4px 4px calc(4px + env(safe-area-inset-bottom)) !important;
  background:rgba(12,12,13,.94);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-top:1px solid #222225;}
div[class*="st-key-tabbar"] [data-testid="stVerticalBlock"]{gap:0 !important;width:100% !important;justify-content:space-around !important;}
div[class*="st-key-tabbar"] [data-testid="stElementContainer"],div[class*="st-key-tabbar"] .element-container,
div[class*="st-key-tabbar"] .stButton,div[class*="st-key-tabbar"] [data-testid="stPopover"]{flex:1 1 0 !important;width:auto !important;min-width:0 !important;}
div[class*="st-key-tabbar"] button{width:100% !important;min-height:54px !important;padding:4px 0 !important;background:transparent !important;border:0 !important;
  box-shadow:none !important;filter:none !important;color:#8E8E96 !important;border-radius:10px !important;}
div[class*="st-key-tabbar"] button>div,div[class*="st-key-tabbar"] button>span{display:flex !important;flex-direction:column !important;align-items:center !important;gap:3px !important;}
div[class*="st-key-tabbar"] button p{font:500 11px var(--sans) !important;letter-spacing:.1px;white-space:nowrap !important;margin:0 !important;color:inherit !important;}
div[class*="st-key-tabbar"] button[kind="primary"]::before,div[class*="st-key-tabbar"] button[data-testid="stBaseButton-primary"]::before{content:"";position:absolute;top:0;left:50%;
  width:22px;height:3px;margin-left:-11px;border-radius:0 0 3px 3px;background:var(--amber);}
div[class*="st-key-tabbar"] button{position:relative !important;}
div[class*="st-key-tabbar"] button [data-testid="stIconMaterial"]{font-size:22px !important;margin:0 !important;color:inherit !important;}
div[class*="st-key-tabbar"] button[kind="primary"],div[class*="st-key-tabbar"] button[data-testid="stBaseButton-primary"]{color:#fff !important;background:transparent !important;box-shadow:none !important;border:0 !important;}
div[class*="st-key-tabbar"] button[kind="primary"] p,div[class*="st-key-tabbar"] button[data-testid="stBaseButton-primary"] p{color:#fff !important;}
div[class*="st-key-tabbar"] button[kind="primary"] [data-testid="stIconMaterial"],
div[class*="st-key-tabbar"] button[data-testid="stBaseButton-primary"] [data-testid="stIconMaterial"]{color:var(--amber) !important;}
div[class*="st-key-tabbar"] [data-testid="stPopover"] button svg{display:none !important;}
div[class*="st-key-tabbar"] button:active{background:rgba(255,255,255,.05) !important;}
[data-testid="stPopoverBody"]{min-width:240px;}
[data-testid="stPopoverBody"] .stButton>button{justify-content:flex-start !important;min-height:46px !important;font-size:15px !important;}

/* buttons, cards, stock page */
.stButton>button,.stDownloadButton>button,[data-testid="stFormSubmitButton"]>button{min-height:44px !important;}
div[class*="st-key-search"] [data-baseweb="select"]>div{min-height:44px;}
.ak-status{flex-direction:column;gap:4px;margin-top:20px;}
.ak-sh{flex-direction:column;gap:10px;padding:14px;} .ak-sh .px{text-align:left;} .ak-sh .nm{font-size:20px;} .ak-sh .px .v{font-size:26px;}
.ak-stats{grid-template-columns:repeat(3,minmax(0,1fr));} .ak-st{padding:9px 10px;border-bottom:1px solid var(--line);}
.ak-rng{padding:10px 14px 14px;}
[data-testid="stMetricValue"]{font-size:17px !important;}
[data-testid="stDataFrame"]{font-size:13px;}

/* landing / sign-in on phones */
.lp-hero{padding:56px 0 24px !important;} .lp-h{margin:56px 0 18px !important;} .lp-nav .links a:not(.go){display:none;}
.lp-end{margin-top:56px !important;}
div[class*="st-key-signin"]{margin-top:28px !important;}
"""

MOBILE_CSS = "<style>@media (max-width:820px){" + _SAFE + "}</style>"
PHONE_CSS = "<style>" + _PHONE + "</style>"
