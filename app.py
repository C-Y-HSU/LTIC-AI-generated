import streamlit as st
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import json
from datetime import datetime

# ==================== 頁面設定 ====================
st.set_page_config(page_title="長照機構感管監測系統", layout="wide", page_icon="🏥")

# ==================== 連線與 API 設定 ====================
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

SYSTEM_INSTRUCTION = """
SYSTEM_INSTRUCTION = """
你是一位嚴謹的長期照護機構感染管制專家 (Infection Control Officer)。
機構現場常態「僅有護理人員與照服員執勤，無常駐醫師」。
請依據台灣疾管署長期照護機構機構內感染監測定義，審查通報資料、判定是否收案，並提出「機構護理與照服端完全可獨立執行」之感管防護與護理處置建議。

【三大核心審查原則】
1. 機構相關感染判定：
   - 必須是照護過程中發生。若在新入住或醫院出院返院時已發生或處於潛伏期，屬機構外帶入，不收為機構內感染。
   - 例外破格收案：若剛返院但住院紀錄證實出院時無該感染，且返院後有明確之「機構護理操作污染/侵入性處置缺失」致急性發病，仍屬照護過程所致之機構內感染。
2. 症狀時序性：必須為「新產生」或「急性惡化」，排除慢性既有狀態。
3. 排除非感染性病理機轉：
   - 呼吸道：排除心衰竭肺水腫、慢性阻塞性肺病急性發作、過敏。
   - 腸胃道：排除軟便劑（Sennoside/MgO/Dulcolax 等調整）、灌食不耐、腸躁症。
   - 泌尿道：無症狀單純菌尿不收案。
   - 結膜炎：排除過敏與創傷；口腔排除牙周炎、蛀牙、疱疹。

【機構端建議處置生成規範（護理與照服端可行性）】
嚴禁產出「醫師開立抗生素/處方」、「進行侵入性手術」等醫療端指令。處置建議必須精準落實於以下四個面向：
1. 防護措施與空間隔離：
   - 標準防護、接觸隔離或飛沫隔離。
   - 床位處置（如拉上隔簾、暫停跨區活動、同寢室住民同質或分區照護、調至最後照護順序）。
2. 護理技術與臨床處置：
   - 呼吸道：姿位引流、翻身拍背、抽痰維持呼吸道暢通、給氧支持（如 O2 Cannula）。
   - 泌尿道：嚴格無菌技術重置導尿管、落實密閉式引流、尿袋嚴禁高於膀胱、加強會陰沖洗 (CDO)。
   - 腸胃道/皮膚：暫停公費或共用餐具、加強稀釋漂白水環境清消、傷口分泌物無菌換藥包紮。
3. 監測與機構內快速篩檢：
   - 監測生命徵象（如 q4h BP/PR/RR/SpO2/耳溫）、I/O 尿量性狀紀錄。
   - 機構內即時篩檢（尿液試紙檢驗 Dipstick、COVID-19/流感抗原快篩）。
4. 醫療通報與外送標準：
   - 記錄異常數值聯絡特約責任醫療院所/特約醫師評估。
   - 設定送醫警訊（如 GCS 意識驟降、SpO2<90% 氧氣無法改善、高燒寒顫休克徵兆即啟動 119 送醫）。

【輸出規範】
嚴格輸出純合法 JSON，格式如下：
{
  "is_case": true 或 false,
  "case_type": "呼吸道感染-肺炎" | "呼吸道感染-類流感" | "呼吸道感染-下呼吸道" | "呼吸道感染-感冒咽喉炎" | "泌尿道感染-有導尿管" | "泌尿道感染-無導尿管" | "腸胃道感染" | "皮膚軟組織感染" | "疥瘡" | "眼耳鼻口感染" | "血流感染" | "無法解釋發燒" | "不收案",
  "is_healthcare_associated": true 或 false,
  "detailed_reasoning": "說明時序與照護相關性、非感染因素排除情況、符合與未符合的指引條款。",
  "recommended_actions": "條列護理端具體處置：1. 感管防護與隔離；2. 護理照護措施；3. 機構內檢驗與生命徵象監測；4. 責任醫院/特約醫師通報與轉送條件。"
}
"""

model = genai.GenerativeModel(
    model_name="gemini-3.8-flash",
    system_instruction=SYSTEM_INSTRUCTION
)

@st.cache_resource
def init_sheets():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open("LTCF_Infection_Surveillance_DB")
    return spreadsheet.worksheet("Residents_Master"), spreadsheet.worksheet("Surveillance_Logs")

try:
    sheet_residents, sheet_logs = init_sheets()
except Exception as e:
    st.error(f"Google 試算表連線失敗，請檢查 secrets 設定與共用權限：{e}")
    st.stop()

# ==================== 護理站端通報介面 ====================
st.title("🏥 護理站感染管制通報與智慧收案系統")
st.caption("依據長期照護機構感染監測指引進行即時判定與雲端登錄")

col_left, col_right = st.columns([1, 1], gap="medium")

# ----- 左欄：基本資料比對與帶入 -----
with col_left:
    st.subheader("1. 住民基本資料比對")
    search_query = st.text_input("輸入床號或姓名搜尋住民", placeholder="例如：301-1 或 王大明")
    
    resident_info = {}
    if search_query:
        try:
            records = sheet_residents.get_all_records()
            match = next((r for r in records if search_query.strip() in str(r.get("床號", "")) or search_query.strip() in str(r.get("姓名", ""))), None)
            if match:
                resident_info = match
                st.success(f"已帶入：{match.get('姓名')}｜床號：{match.get('床號')}｜管路：{match.get('留置管路', '無')}")
            else:
                st.warning("查無此住民名冊資料，請手動填寫以下欄位。")
        except Exception as err:
            st.warning(f"名冊讀取提示：{err}")

    bed_num = st.text_input("床號 *", value=str(resident_info.get("床號", "")))
    name = st.text_input("住民姓名 *", value=str(resident_info.get("姓名", "")))
    
    catheter_options = ["無導尿管", "留置導尿管 (Foley)", "恥骨上造廔管"]
    default_catheter_idx = 1 if "導尿管" in str(resident_info.get("留置管路", "")) else 0
    catheter_status = st.selectbox("導尿管留置狀態", catheter_options, index=default_catheter_idx)
    
    has_recent_adm = st.radio(
        "近期（72小時內）是否有新入住或外院出院返院？ *",
        ["否（常態在院照護）", "是（剛入住或自醫院出院返院）"],
        help="用以判斷是否為潛伏期帶入或原住院之醫療相關感染"
    )

# ----- 右欄：症狀與病程經過 -----
with col_right:
    st.subheader("2. 症狀與病程經過輸入")
    onset_date = st.date_input("症狀發生日期", datetime.today())
    body_temp = st.number_input("最高體溫 (耳溫 ℃)", min_value=35.0, max_value=42.0, value=36.8, step=0.1)
    
    symptoms = st.multiselect(
        "勾選新發生或急性惡化之症狀",
        [
            "咳嗽/乾咳", "咳膿痰或痰量顯著增加", "呼吸急促/呼吸音異常(喘鳴/囉音)",
            "排尿灼熱感/頻尿/急尿", "尿液混濁/惡臭/沉澱物/肉眼血尿", "肋脊角或恥骨上壓痛",
            "稀便或水便(24h≥2次)", "嘔吐(24h≥2次)",
            "傷口或皮膚紅腫熱痛", "傷口或皮膚膿性分泌物", "發癢斑丘疹",
            "結膜充血發紅(>24h)", "眼部膿性分泌物(>24h)", "意識或活動功能明顯惡化"
        ]
    )
    
    clinical_note = st.text_area(
        "護理病程經過與處置記錄 *",
        placeholder="請詳細記錄臨床徵候、用藥異動（如是否剛吃軟便劑）、是否照過胸部X光、尿液檢驗數據等...",
        height=140
    )

submit_btn = st.button("送出並執行感管收案審核", type="primary", use_container_width=True)

# ==================== 3. 判定與存檔 ====================
if submit_btn:
    if not bed_num.strip() or not name.strip() or not clinical_note.strip():
        st.error("請務必完整填寫：床號、住民姓名以及護理病程經過！")
    else:
        with st.spinner("感染管制專家引擎分析中，正在比對感管指引..."):
            prompt_content = f"""
            住民資訊：
            - 床號：{bed_num}
            - 姓名：{name}
            - 導尿管留置狀態：{catheter_status}
            - 近期出入院狀況：{has_recent_adm}
            - 症狀發生日：{onset_date}
            - 體溫：{body_temp} ℃
            - 勾選症狀：{", ".join(symptoms) if symptoms else "未特別勾選，詳見病程"}
            - 護理病程記錄：{clinical_note}
            """
            
            try:
                # 啟用結構化 JSON 強制輸出，避免模型輸出 Markdown 標籤干擾解析
                response = model.generate_content(
                    prompt_content,
                    generation_config={"response_mime_type": "application/json"}
                )
                
                raw_text = response.text.strip()
                
                # 自動清理可能的 Markdown 標記
                if raw_text.startswith("```"):
                    raw_text = raw_text.strip("`")
                    if raw_text.startswith("json"):
                        raw_text = raw_text[4:].strip()

                result = json.loads(raw_text)
                
                # 畫面結果展示
                st.divider()
                if result.get("is_case"):
                    st.error(f"🚨 【符合收案條件】感染分類：{result.get('case_type', '未分類')}")
                else:
                    st.info(f"📋 【未符合收案標準】原因類別：{result.get('case_type', '不收案')}")
                
                st.markdown(f"**照護相關判定：** {'機構內感染' if result.get('is_healthcare_associated') else '非機構照護所致／院外帶入'}")
                st.markdown(f"**審核與推理依據：**\n{result.get('detailed_reasoning')}")
                st.warning(f"**建議防護措施：**\n{result.get('recommended_actions')}")
                
                # 寫入 Google Sheets (Surveillance_Logs)
                with st.spinner("正在同步寫入 Google 試算表資料庫..."):
                    log_entry = [
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        bed_num,
                        name,
                        catheter_status,
                        has_recent_adm,
                        str(onset_date),
                        body_temp,
                        ", ".join(symptoms),
                        clinical_note,
                        "收案" if result.get("is_case") else "不收案",
                        result.get("case_type", "未分類"),
                        "是" if result.get("is_healthcare_associated") else "否",
                        result.get("detailed_reasoning", ""),
                        result.get("recommended_actions", ""),
                        "待複核"
                    ]
                    sheet_logs.append_row(log_entry)
                    st.success("✅ 通報記錄與判定結果已成功同步存入 Google 試算表！")
                
            except json.JSONDecodeError:
                st.error("JSON 格式轉換失敗，模型原始輸出內容如下：")
                st.code(response.text)
            except Exception as e:
                st.error(f"執行或寫入試算表時發生錯誤：{e}")
