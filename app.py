import streamlit as st
import requests
import base64
import re
import json
from datetime import datetime, date
from PIL import Image
from io import BytesIO
import pandas as pd
from difflib import SequenceMatcher

# ------------------------------
# 1. 页面配置
# ------------------------------
st.set_page_config(page_title="论文打卡核验系统", layout="wide")
st.title("📚 社群论文打卡 · 自动核验与排行榜")

# ------------------------------
# 2. 从 secrets 读取密钥（已在 GitHub/Streamlit 设置）
# ------------------------------
BAIDU_API_KEY = st.secrets["BAIDU_API_KEY"]
BAIDU_SECRET_KEY = st.secrets["BAIDU_SECRET_KEY"]
FEISHU_APP_ID = st.secrets["FEISHU_APP_ID"]
FEISHU_APP_SECRET = st.secrets["FEISHU_APP_SECRET"]
FEISHU_APP_TOKEN = st.secrets["FEISHU_APP_TOKEN"]
FEISHU_TABLE_ID = st.secrets["FEISHU_TABLE_ID"]
FEISHU_MEMBER_TABLE_ID = st.secrets.get("FEISHU_MEMBER_TABLE_ID", None)  # 可选

# 相似度阈值（0.75 表示75%相似即通过）
THRESHOLD = 0.75

# ------------------------------
# 3. 百度OCR获取access_token
# ------------------------------
def get_baidu_access_token():
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": BAIDU_API_KEY,
        "client_secret": BAIDU_SECRET_KEY
    }
    res = requests.post(url, params=params)
    return res.json().get("access_token")

# ------------------------------
# 4. 调用百度通用文字识别（标准版）
# ------------------------------
def baidu_ocr(image_bytes):
    token = get_baidu_access_token()
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={token}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    img_base64 = base64.b64encode(image_bytes).decode()
    data = {"image": img_base64}
    resp = requests.post(url, headers=headers, data=data)
    result = resp.json()
    if "words_result" in result:
        return [item["words"] for item in result["words_result"]]
    else:
        st.error(f"OCR识别失败：{result}")
        return []

# ------------------------------
# 5. 从飞书多维表格获取今日论文摘要
# ------------------------------
def get_feishu_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    res = requests.post(url, headers=headers, json=payload)
    return res.json().get("tenant_access_token")

def fetch_today_abstract():
    token = get_feishu_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    # 筛选：发布日期 = 今天
    today_str = date.today().strftime("%Y-%m-%d")
    # 注意：飞书多维表格日期字段过滤语法为 '字段名 = "值"'
    # 这里简单查询前10条，取发布日期匹配今天的；若没有则取最新一条
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    params = {"page_size": 10}
    resp = requests.get(url, headers=headers, params=params)
    records = resp.json().get("data", {}).get("items", [])
    
    for rec in records:
        fields = rec.get("fields", {})
        pub_date = fields.get("发布日期")
        if pub_date:
            # 飞书返回日期格式可能为 "2026-02-13"
            if pub_date.startswith(today_str):
                abstract = fields.get("论文摘要", "")
                return abstract.strip()
    # 没找到当天的，取第一条（假设管理员已录入）
    if records:
        fields = records[0].get("fields", {})
        return fields.get("论文摘要", "").strip()
    return ""

# ------------------------------
# 6. 从OCR文本中解析打卡信息（适配你的小红书模版）
# ------------------------------
def parse_checkin(text_lines):
    """
    输入：OCR识别的文本行列表
    输出：列表，每个元素为 (昵称, 打卡时间, 摘录句子)
    """
    entries = []
    i = 0
    while i < len(text_lines):
        line = text_lines[i].strip()
        # 匹配昵称行：格式如 "昵称（仅中文/英文/数字且最好不要重名）：张三"
        nick_match = re.match(r'^昵称.*?[:：]\s*(.*?)$', line)
        if nick_match:
            nickname = nick_match.group(1).strip()
            # 检查下一行是否是打卡时间
            if i+1 < len(text_lines):
                time_line = text_lines[i+1].strip()
                time_match = re.match(r'^打卡时间.*?[:：]\s*(\d{4}/\d{1,2}/\d{1,2})', time_line)
                if time_match:
                    punch_time = time_match.group(1).strip()
                    # 再下一行是摘要句子
                    if i+2 < len(text_lines):
                        abstract_line = text_lines[i+2].strip()
                        # 去除可能的前缀
                        abstract_sentence = re.sub(r'^论文(原文)?摘要的随机一句话.*?[:：]', '', abstract_line).strip()
                        entries.append((nickname, punch_time, abstract_sentence))
                        i += 3
                        continue
        i += 1
    return entries

# ------------------------------
# 7. 相似度计算（简单文本匹配）
# ------------------------------
def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

# ------------------------------
# 8. 从飞书获取成员昵称列表（用于模糊匹配）
# ------------------------------
def fetch_member_nicknames():
    if not FEISHU_MEMBER_TABLE_ID:
        return []
    token = get_feishu_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_MEMBER_TABLE_ID}/records"
    resp = requests.get(url, headers=headers)
    records = resp.json().get("data", {}).get("items", [])
    nicknames = []
    for rec in records:
        fields = rec.get("fields", {})
        nick = fields.get("昵称", "")
        if nick:
            nicknames.append(nick.strip())
    return nicknames

# ------------------------------
# 9. 会话状态初始化（存储打卡记录）
# ------------------------------
if "records" not in st.session_state:
    st.session_state.records = []  # 每条为 (昵称, 打卡日期, 摘要, 是否通过, 相似度)
if "pending_review" not in st.session_state:
    st.session_state.pending_review = []  # 待复核（相似度低于阈值或昵称不匹配）

# ------------------------------
# 10. 主界面：上传与核验
# ------------------------------
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📤 上传打卡截图")
    uploaded_files = st.file_uploader("支持PNG/JPG，可多选（长截图建议拆分为单人或直接上传整张）",
                                      type=["png", "jpg", "jpeg"],
                                      accept_multiple_files=True)
    
    if uploaded_files:
        # 获取当日标准摘要
        standard_abstract = fetch_today_abstract()
        if not standard_abstract:
            st.warning("⚠️ 今日论文摘要未录入飞书多维表格，请管理员补录。")
        else:
            st.info(f"📄 今日论文摘要（前100字）：{standard_abstract[:100]}...")
        
        member_nicknames = fetch_member_nicknames()
        
        for uploaded_file in uploaded_files:
            # 读取图片字节
            image_bytes = uploaded_file.read()
            # OCR识别
            with st.spinner(f"正在识别 {uploaded_file.name} ..."):
                text_lines = baidu_ocr(image_bytes)
            
            if not text_lines:
                st.error(f"{uploaded_file.name} 识别失败，请检查图片是否清晰。")
                continue
            
            # 解析打卡条目
            entries = parse_checkin(text_lines)
            if not entries:
                st.warning(f"{uploaded_file.name} 未检测到符合格式的打卡信息，请确认截图包含三行规范文本。")
                continue
            
            st.success(f"{uploaded_file.name} 共识别出 {len(entries)} 条打卡记录")
            
            # 逐条处理
            for nickname, punch_time, sentence in entries:
                # 日期有效性：必须是今天（可自定义）
                is_date_valid = (punch_time == date.today().strftime("%Y/%m/%d"))
                
                # 相似度计算（如果标准摘要存在）
                if standard_abstract:
                    sim = similarity(sentence, standard_abstract)
                    is_sim_pass = sim >= THRESHOLD
                else:
                    sim = 0.0
                    is_sim_pass = False
                
                # 昵称有效性：若配置了成员表，检查是否在表中
                nick_valid = True
                if member_nicknames:
                    # 简单包含匹配（可改为模糊匹配）
                    if not any(nickname in m or m in nickname for m in member_nicknames):
                        nick_valid = False
                
                # 整体是否通过（日期必须今天，相似度必须达标）
                passed = is_date_valid and is_sim_pass and nick_valid
                
                # 记录
                record = {
                    "昵称": nickname,
                    "打卡日期": punch_time,
                    "摘录句子": sentence,
                    "相似度": round(sim, 2),
                    "日期有效": is_date_valid,
                    "相似度达标": is_sim_pass,
                    "昵称有效": nick_valid,
                    "通过": passed
                }
                st.session_state.records.append(record)
                
                if not passed:
                    st.session_state.pending_review.append(record)
            
            # 显示本次识别结果
            df_temp = pd.DataFrame(entries, columns=["昵称", "打卡时间", "摘录句子"])
            st.dataframe(df_temp, use_container_width=True)

# ------------------------------
# 11. 待复核面板（管理员手动修正）
# ------------------------------
with col2:
    st.subheader("🛠 待复核条目")
    if st.session_state.pending_review:
        review_df = pd.DataFrame(st.session_state.pending_review)
        st.dataframe(review_df)
        
        # 简单修正：一键强制通过（实际可设计下拉选择）
        if st.button("将选中的条目强制标记为通过"):
            # 简化：全部强制通过（正式环境可加交互）
            for rec in st.session_state.pending_review:
                rec["通过"] = True
            st.session_state.pending_review.clear()
            st.success("已强制通过所有待复核条目，请刷新页面查看排行榜。")
            st.experimental_rerun()
    else:
        st.info("当前无待复核条目")

# ------------------------------
# 12. 打卡排行榜（日/周/月）
# ------------------------------
st.markdown("---")
st.subheader("🏆 打卡排行榜")

if st.session_state.records:
    df = pd.DataFrame(st.session_state.records)
    # 仅统计通过的有效打卡
    valid_df = df[df["通过"] == True]
    
    if not valid_df.empty:
        # 按昵称分组计数
        rank = valid_df.groupby("昵称").size().reset_index(name="打卡次数")
        rank = rank.sort_values("打卡次数", ascending=False).reset_index(drop=True)
        
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("📊 累计打卡次数榜")
            st.dataframe(rank, use_container_width=True)
            
            # 简单图表
            st.bar_chart(rank.set_index("昵称")["打卡次数"])
        
        with col_b:
            # 按日期筛选
            st.write("📅 按日期查看")
            date_options = valid_df["打卡日期"].unique()
            selected_date = st.selectbox("选择日期", sorted(date_options, reverse=True))
            daily_df = valid_df[valid_df["打卡日期"] == selected_date]
            daily_rank = daily_df.groupby("昵称").size().reset_index(name="当日打卡次数")
            daily_rank = daily_rank.sort_values("当日打卡次数", ascending=False)
            st.dataframe(daily_rank, use_container_width=True)
    else:
        st.info("暂无有效打卡记录")
else:
    st.info("暂无打卡记录，请上传截图")

# ------------------------------
# 13. 管理员工具（摘要录入提醒、导出记录）
# ------------------------------
with st.expander("🔧 管理员工具"):
    st.write("当前存储的打卡记录条数：", len(st.session_state.records))
    if st.button("清空当前所有记录（慎用）"):
        st.session_state.records = []
        st.session_state.pending_review = []
        st.success("已清空")
        st.experimental_rerun()
    
    # 导出为CSV
    if st.session_state.records:
        export_df = pd.DataFrame(st.session_state.records)
        csv = export_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出全部记录为CSV", csv, "punch_records.csv", "text/csv")
    
    st.caption("💡 每日请确保飞书多维表格中已录入当天论文摘要，系统会自动拉取。")
