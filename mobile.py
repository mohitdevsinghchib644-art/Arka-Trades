"""mobile.py — phone layout for the Arka terminal. Inject once after APP_CSS: st.markdown(MOBILE_CSS, unsafe_allow_html=True)"""

MOBILE_CSS = """<style>
/* the 0-height cookie script frame must not take space */
[data-testid="stElementContainer"]:has(iframe[height="0"]){display:none !important;}

@media (max-width:768px){
  .block-container{padding:0 10px 86px !important;}      /* room for the bottom nav */
  [data-testid="stVerticalBlock"]{gap:.6rem;}

  /* top bar: brand + status + user in ONE slim row, not sticky (frees the screen, lets the nav be fixed) */
  div[class*="st-key-topbar"]{position:static !important;backdrop-filter:none !important;margin:0 -10px;padding:0 10px;}
  div[class*="st-key-topbar"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important;align-items:center;gap:6px !important;}
  div[class*="st-key-topbar"] [data-testid="stColumn"]{min-width:0 !important;width:auto !important;flex:1 1 auto !important;}
  .ak-brand{height:40px;} .ak-bs,.ak-bn{display:none;}
  .ak-chips{height:40px;} .ak-chips .ak-chip:nth-child(2){display:none;}       /* hide the clock chip */
  div[class*="st-key-topuser"] [data-testid="stPopover"]>div>button{min-height:30px !important;}

  /* main navigation -> fixed bottom bar, swipe sideways for the rest */
  div[class*="st-key-topnav"]{position:fixed !important;left:0;right:0;bottom:0;z-index:999;background:#000;
    border-top:1px solid var(--line2);padding:0 2px env(safe-area-inset-bottom,0px);}
  div[class*="st-key-topnav"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important;overflow-x:auto;gap:0 !important;}
  div[class*="st-key-topnav"] [data-testid="stColumn"]{flex:0 0 auto !important;width:auto !important;min-width:92px !important;}
  div[class*="st-key-topnav"] .stButton>button{min-height:54px !important;font-size:9.5px !important;border-bottom:0 !important;border-top:2px solid transparent !important;}
  div[class*="st-key-topnav"] .stButton>button[kind="primary"],
  div[class*="st-key-topnav"] button[data-testid^="stBaseButton-primary"]{border-bottom:0 !important;border-top:2px solid var(--amber) !important;}

  /* command line: one full-width box (no GO label, no recent chips); 16px stops iOS zooming on focus */
  div[class*="st-key-cmdrow"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important;}
  div[class*="st-key-cmdrow"] [data-testid="stColumn"]:first-child{display:none !important;}
  div[class*="st-key-cmdrow"] [data-testid="stColumn"]:has(div[class*="st-key-recent"]){display:none !important;}
  div[class*="st-key-cmdrow"] .stTextInput input,div[class*="st-key-cmdrow"] [data-baseweb="select"] input,
  .stTextInput input,.stNumberInput input,.stTextArea textarea{font-size:16px !important;}

  /* headers */
  .ak-ph{flex-direction:column;align-items:flex-start;gap:8px;padding:10px 2px;} .ak-ph .t{font-size:18px;} .ak-ph .d{display:none;}
  .ak-sh{flex-direction:column;gap:12px;} .ak-sh .px{text-align:left;} .ak-sh .px .v{font-size:26px;} .ak-sh .nm{font-size:18px;}
  .ak-status{flex-direction:column;gap:4px;margin-top:14px;}

  /* market monitor */
  .hm-head .t small{display:none;} .hm-head .t{font-size:14px;} .hm-hv{font-size:32px;}
  .hm-stats{grid-template-columns:repeat(2,minmax(0,1fr));}
  .hm-pt{padding:0 12px;} .hm-pb{padding:12px 12px 10px;} .hm-t td{padding:7px 10px;}
  .hm-tiles{max-width:100%;} .hm-tile{width:calc(25% - 3px);min-width:72px;height:56px;}
  .hm-fhead,div[class*="st-key-hm_fn_"]{display:none !important;}                /* function cards: bottom nav replaces them */
  div[class*="st-key-newsrail"]{display:none !important;}                        /* side news rail (use the NEWS tab) */
}
</style>"""
