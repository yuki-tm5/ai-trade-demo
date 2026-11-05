from pathlib import Path
import sys,json
import streamlit as st
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from ask_find import ask,config,resolve_file
st.set_page_config(page_title="AI Trade · 规则检索 Demo",layout="wide")
st.title("AI Trade Operations · 规则检索 Demo")
st.caption("完全虚构数据 · 不使用AI语义理解 · 报价不代表订单，图纸不代表获准生产")
labels={"工厂报价":"factory_quotes","采购记录":"purchase_record","材料与交货日期":"material_purchase","工程图与版本":"engineering_versions","报价资料":"quote_documents"}
with st.form("search"):
    intent=st.selectbox("查询类型",list(labels))
    product=st.text_input("完整产品或原型编号",value="ZZ81001")
    number=st.text_input("单据编号（可选）")
    submitted=st.form_submit_button("查询")
st.info("可试：ZZ81001 报价；ZZ81002 材料采购；ZZ81003 / ZZ81003F 多产品；GV40159F 原型图纸；GV82001 无关审计；ZZ81004 版本；ZZ89999 无匹配。")
if submitted:
    q={"intent":labels[intent],"product_id":product,"dataset":"supplementary"}
    if number:q["document_number"]=number
    st.session_state["result"]=ask(ROOT/'config.json',q)
if "result" in st.session_state:
    r=st.session_state["result"]
    st.subheader("查询状态："+r["query_status"])
    for bucket,title in [("documents","相关文件"),("uncertain_documents","待确认文件"),("audit_documents","审计证据（不推荐）")]:
        st.subheader(title)
        if not r[bucket]:st.caption("无")
        for i,d in enumerate(r[bucket]):
            st.write(" / ".join(d["filenames"]))
            st.caption("SHA-256："+d["attachment_id"]+" · 邮件来源："+str(len(d["sources"])))
            p=resolve_file(config(ROOT/'config.json'),d["file_reference"])
            st.download_button("下载原始附件",p.read_bytes(),file_name=d["filenames"][0],key=bucket+str(i))
            with st.expander("查看文件与邮件证据"):st.json(d)
    st.subheader("字段与定位证据")
    st.json(r["fields"])
    with st.expander("限制与业务提示"):st.write(r["warnings"])
    st.download_button("下载完整查询结果",json.dumps(r,ensure_ascii=False,indent=2).encode(),file_name="query_result.json")
