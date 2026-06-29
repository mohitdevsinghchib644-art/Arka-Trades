import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

def render_quant_analysis():
    st.title("📊 Quant Analysis")
    st.caption("Arka Trades • Advanced Market Analytics Platform")
    
    # Create tabs for the different quantitative models shown in your references
    tab1, tab2, tab3 = st.tabs(["Vanna Model", "Theta & Implied Volatility", "Options Flow"])
    
    # ----------------------------------------------------
    # TAB 1: VANNA MODEL
    # ----------------------------------------------------
    with tab1:
        st.subheader("SPX Vanna Model")
        st.write("Tracking Delta Notional across strikes.")
        
        # Mock data generation mirroring your reference image
        strikes = np.linspace(3988, 4250, 100)
        # Creating a U-shape/skew curve for Vanna Flow
        vanna_line1 = 1.75e12 + (strikes - 4130)**2 * 15000000
        vanna_line2 = 1.63e12 + (strikes - 4158)**2 * 28000000
        
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor('#0e1117')
        ax.set_facecolor('#0e1117')
        
        ax.plot(strikes, vanna_line1, color='#8a9ba8', label='Apr 19', linewidth=2)
        ax.plot(strikes, vanna_line2, color='#b388ff', label='Apr 20', linewidth=2)
        
        ax.set_title("Open Interest Vanna Flow", color='white', fontsize=14, pad=15)
        ax.set_xlabel("Strike", color='#8a9ba8')
        ax.set_ylabel("Delta Notional", color='#8a9ba8')
        ax.tick_params(colors='#8a9ba8')
        ax.grid(True, color='#262730', linestyle='--')
        ax.legend(facecolor='#0e1117', edgecolor='#262730', labelcolor='white')
        
        # Format axes to match billions/trillions notation ($1.9T)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${x*1e-12:.1f}T"))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${int(x):,}"))
        
        st.pyplot(fig)

    # ----------------------------------------------------
    # TAB 2: THETA DECAY & LOCAL VOLATILITY SURFACE
    # ----------------------------------------------------
    with tab2:
        st.subheader("0DTE Implied Volatility & Theta Decay")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Non-Linear Theta Decay Curve**")
            days = np.linspace(90, 0, 100)
            # Standard square root time decay curve approximation
            theta_value = np.sqrt(days / 90)
            
            fig2, ax2 = plt.subplots(figsize=(5, 4))
            fig2.patch.set_facecolor('#0e1117')
            ax2.set_facecolor('#0e1117')
            
            ax2.plot(days, theta_value, color='#4caf50', linewidth=2)
            ax2.fill_between(days, theta_value, color='#4caf50', alpha=0.1)
            
            ax2.set_xlim(90, 0) # Invert axis to show countdown to 0 days
            ax2.set_xlabel("Time Until Expiration", color='#8a9ba8')
            ax2.set_ylabel("Time Value", color='#8a9ba8')
            ax2.set_xticks([90, 60, 30, 0])
            ax2.set_xticklabels(["90 days", "60 days", "30 days", "0"], color='#8a9ba8')
            ax2.tick_params(colors='#8a9ba8')
            ax2.grid(False)
            st.pyplot(fig2)
            
        with col2:
            st.markdown("**Local Volatility Surface (Moneyness vs Time)**")
            # 3D surface mesh generation
            moneyness = np.linspace(0.4, 1.6, 30)
            time = np.linspace(0, 0.4, 30)
            M, T = np.meshgrid(moneyness, time)
            
            # Mathematical representation of Vol Skew decaying over time
            Z = 0.3 + (1.0 - M)**2 * 0.5 + (0.4 - T) * 0.2
            
            fig3 = plt.figure(figsize=(5, 4))
            fig3.patch.set_facecolor('#0e1117')
            ax3 = fig3.add_subplot(111, projection='3d')
            ax3.set_facecolor('#0e1117')
            
            surf = ax3.plot_surface(M, T, Z, cmap='jet', edgecolor='none', alpha=0.9)
            
            ax3.set_xlabel('Moneyness', color='#8a9ba8')
            ax3.set_ylabel('Time', color='#8a9ba8')
            ax3.tick_params(colors='#8a9ba8')
            # Adjust view to match standard vol surface perspectives
            ax3.view_init(elev=30, azim=-60)
            
            st.pyplot(fig3)

    # ----------------------------------------------------
    # TAB 3: OPTIONS FLOW
    # ----------------------------------------------------
    with tab3:
        st.subheader("Open Interest Options Inventory Flow")
        
        # Simulating Call vs Put distribution profile
        strikes_oi = np.arange(7220, 7960, 20)
        call_oi = np.random.randint(500, 5000, size=len(strikes_oi))
        put_oi = np.random.randint(-5000, -500, size=len(strikes_oi))
        
        fig4, ax4 = plt.subplots(figsize=(12, 5))
        fig4.patch.set_facecolor('#0e1117')
        ax4.set_facecolor('#0e1117')
        
        ax4.bar(strikes_oi, call_oi, color='#00c853', width=12, label='Call OI')
        ax4.bar(strikes_oi, put_oi, color='#ff1744', width=12, label='Put OI')
        
        # Spot reference line indicators
        ax4.axvline(x=7599, color='white', linestyle='--', alpha=0.7, label='Spot Price: 7599')
        ax4.axvline(x=7570, color='#ff9100', linestyle=':', alpha=0.7, label='Put Support / HVL')
        
        ax4.set_title("Open Interest Chart for SPX", color='white', fontsize=12, loc='left')
        ax4.set_xlabel("Strikes", color='#8a9ba8')
        ax4.tick_params(colors='#8a9ba8', axis='both')
        plt.xticks(rotation=90)
        ax4.grid(True, color='#262730', alpha=0.3)
        ax4.legend(facecolor='#0e1117', edgecolor='#262730', labelcolor='white')
        
        st.pyplot(fig4)
