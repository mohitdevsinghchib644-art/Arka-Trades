"""mobile.py — phone layout for the Arka terminal (v2).

app.py injects MOBILE_CSS on every page (it applies below 820 px through a media query) and, when the device is a
phone, PHONE_CSS too (the same rules without the media query, so they apply even if the browser reports an odd
width). Every rule degrades gracefully: if Streamlit changes its markup, the page simply falls back to Streamlit's
own stacked layout instead of breaking.
"""

_RULES = """
.block-container{padding:0 12px 56px !important;}
[data-testid="stVerticalBlock"]{gap:.7rem;}
[data-testid="stHorizontalBlock"]{gap:.6rem !important;}
[data-testid="stMarkdownContainer"]{overflow-x:auto;}                       /* wide tables scroll inside their box */
iframe{max-width:100%;}

/* header: logo, name and market dot only */
.ak-hdr{padding:10px 0 8px;} .ak-hdr .sub,.ak-hdr .clk,.ak-hdr .det{display:none !important;}
.ak-hdr .nm{font-size:15px;}

/* main navigation: one row that scrolls sideways, pinned to the top while you scroll */
div[class*="st-key-navbar"]{position:sticky;top:0;z-index:60;background:var(--bg);margin:0 -12px !important;padding:4px 12px 8px;
  border-bottom:1px solid var(--line);}
div[class*="st-key-navbar"] [role="radiogroup"]{flex-wrap:nowrap !important;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch;
  padding-bottom:2px;}
div[class*="st-key-navbar"] [role="radiogroup"]::-webkit-scrollbar{display:none;}
div[class*="st-key-navbar"] label[data-baseweb="radio"]{flex:0 0 auto !important;padding:8px 12px !important;}
div[class*="st-key-navbar"] label[data-baseweb="radio"] p{font-size:14px !important;}

/* every view switcher (radio pills) and tab bar scrolls sideways instead of wrapping into a block */
[data-testid="stRadio"] [role="radiogroup"]{flex-wrap:nowrap !important;overflow-x:auto;scrollbar-width:none;}
[data-testid="stRadio"] [role="radiogroup"]::-webkit-scrollbar{display:none;}
[data-testid="stRadio"] label[data-baseweb="radio"]{flex:0 0 auto !important;}
.stTabs [data-baseweb="tab-list"]{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none;}

/* touch targets; 16 px text stops iOS zooming into a field */
.stTextInput input,.stNumberInput input,.stTextArea textarea,[data-baseweb="select"] input,.stDateInput input{font-size:16px !important;}
.stButton>button,.stDownloadButton>button,[data-testid="stFormSubmitButton"]>button{min-height:42px !important;}
[data-testid="stPopover"]{width:100%;}

/* page header and footer */
.ak-ph{padding:12px 0 8px;} .ak-ph .t{font-size:19px;} .ak-ph .d{display:none;}
.ak-status{flex-direction:column;gap:4px;}

/* security page */
.ak-sh{flex-direction:column;gap:10px;padding:14px;} .ak-sh .px{text-align:left;} .ak-sh .px .v{font-size:26px;} .ak-sh .nm{font-size:19px;}
.ak-stats{grid-template-columns:repeat(3,minmax(0,1fr)) !important;}

/* home (market monitor) */
.hm-head .t small{display:none;} .hm-head .t{font-size:14px;} .hm-hv{font-size:32px;}
.hm-stats{grid-template-columns:repeat(2,minmax(0,1fr));}
.hm-tiles{max-width:100%;} .hm-tile{width:calc(25% - 3px);min-width:70px;height:54px;}
.hm-pb{padding:12px 12px 10px;} .hm-pt{padding:0 12px;} .hm-t td{padding:7px 10px;}
.hm-fhead,div[class*="st-key-hm_fn_"]{display:none !important;}           /* function cards: the nav bar replaces them */

/* landing + sign-in */
.lp-hero{padding:56px 4px 28px !important;} .lp-nav .links a:not(.go){display:none;}
"""

MOBILE_CSS = "<style>@media (max-width:820px){" + _RULES + "}</style>"
PHONE_CSS = "<style>" + _RULES + "</style>"
