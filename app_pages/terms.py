"""Terms of Use: what Cloudzy is for, its limits, and who owns what (text in src.terms).

Reached from the sidebar footer and from Settings (it isn't listed in the sidebar menu). There's no pop-up or
"accept" button: the terms are there to read, and they apply to using the app.
"""

from __future__ import annotations

import streamlit as st

from src import terms as TU
from src import theme as T

st.html(T.page_header("Terms of Use", "About · legal", "gavel",
                      "How Cloudzy may be used, what its forecasts and predictions can and can't do, and who owns what."))
st.html("""<style>
.st-key-cz-terms{max-width:48rem}
.st-key-cz-terms h3{scroll-margin-top:4.5rem}
.st-key-cz-terms li{margin-bottom:.15rem}
.st-key-cz-terms table{font-size:.9rem;display:block;max-width:100%;overflow-x:auto}
</style>""")

with st.container(key="cz-terms"):
    st.markdown(f"**Last updated: {TU.LAST_UPDATED}**")
    st.markdown("**In short:** Cloudzy's forecasts, alerts and predictions are estimates that can be wrong. "
                "It isn't an emergency or official warning service, so for anything that matters, check an "
                "official source. The code is open source under the MIT licence; data and components from "
                "others keep their own terms.")

    with st.expander("Contents", expanded=False, icon=":material/toc:"):
        st.markdown("\n".join(f"{i}. [{s.title}](#{s.anchor})" for i, s in enumerate(TU.SECTIONS, 1)))

    for i, s in enumerate(TU.SECTIONS, 1):
        st.subheader(f"{i}. {s.title}", anchor=s.anchor, divider="gray")
        st.markdown(s.body.strip())
        if s.review:
            st.caption(f":material/rate_review: **Review note:** {s.review}")

    st.divider()
    st.caption("This text explains the terms in plain language. It isn't legal advice and hasn't been reviewed by "
               "a lawyer.")
