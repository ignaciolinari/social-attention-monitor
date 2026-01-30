"""
SAM Dashboard

Streamlit-based dashboard for visualizing social attention data.
"""

import streamlit as st

st.set_page_config(
    page_title="SAM - Social Attention Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    """Main dashboard entry point."""
    st.title("📊 Social Attention Monitor")
    st.markdown("*Real-time social attention tracking for film & TV releases*")

    # Sidebar
    with st.sidebar:
        st.header("🎯 Navigation")
        page = st.radio(
            "Select View",
            ["🔥 Trending Now", "📈 Time Series", "🔄 Platform Comparison", "💬 Sentiment"],
            label_visibility="collapsed",
        )

        st.divider()
        st.header("⚙️ Settings")
        st.selectbox("Time Range", ["Last 24 hours", "Last 7 days", "Last 30 days"])

    # Main content
    if page == "🔥 Trending Now":
        st.header("🔥 Trending Now")
        st.info("This view will show top titles ranked by Attention Index.")
        st.markdown("""
        **Coming soon:**
        - Top 10 trending titles
        - Attention Index scores
        - Velocity indicators
        - Quick sentiment overview
        """)

    elif page == "📈 Time Series":
        st.header("📈 Time Series Analysis")
        st.info("This view will show engagement and sentiment over time.")
        st.markdown("""
        **Coming soon:**
        - Mentions over time
        - Sentiment trajectory
        - Engagement velocity
        - Release date markers
        """)

    elif page == "🔄 Platform Comparison":
        st.header("🔄 Platform Comparison")
        st.info("This view will compare engagement across Reddit and YouTube.")
        st.markdown("""
        **Coming soon:**
        - Side-by-side platform metrics
        - Correlation analysis
        - Platform-specific trends
        """)

    elif page == "💬 Sentiment":
        st.header("💬 Sentiment Distribution")
        st.info("This view will show sentiment breakdown for selected titles.")
        st.markdown("""
        **Coming soon:**
        - Positive/Neutral/Negative distribution
        - Sentiment volatility
        - Word clouds
        - Top positive/negative comments
        """)

    # Footer
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption("🟢 Demo Mode Active")
    with col2:
        st.caption("Last updated: --")
    with col3:
        st.caption("SAM v0.1.0")


if __name__ == "__main__":
    main()
