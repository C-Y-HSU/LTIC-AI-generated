import streamlit as st
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time
from datetime import datetime

# ==================== 頁面設定 ====================
st.set_page_config(page_title="長照機構感管監測系統", layout="wide", page_icon="🏥")

# ==================== 初始化 Session State ====================
if "audit_result" not in st.session_state:
    st.session_state.audit_result = None
if "current_row_idx" not in st.session_state:
    st.session_state.current_row_idx = None
if "base_payload" not in st.session_state:
    st.session_state.base_payload = None

# ==================== 連線與 API 設定 ====================
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

SYSTEM_INSTRUCTION = """
你是一位嚴謹的老人福利機構（長期照護機構）感染管制專家 (Infection Control Officer)。
【機構環境鐵律】：本機構為老人福利機構，現場「僅有護理人員與照顧服務員」，平日「完全沒有常駐醫師、沒有醫檢師、沒有放射師」。
請依據台灣疾管署長期照護機構機構內感染監測定義，審查通報資料與時序性，判定是否符合收案條件。

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

【護理端處置建議鐵律（嚴禁醫療與醫檢指令）】
在生成 `nursing_measures` 時，必須 100% 立足於「機構護理師與照服員獨立可執行」的照護動作：
- ❌ **絕對禁止出現**：開立抗生素/藥物處方、安排抽血(CBC/CRP/Blood Culture)、照胸部X光(CXR)、留取細菌培養(C/S)等醫院端才能執行的醫療與醫檢行為。
- ✅ **必須聚焦於以下純護理與照護措施**：
  1. 感管隔離與動線：採標準/飛沫/接觸隔離、拉上床位隔簾、暫停跨區共餐與團體活動、調整為該區最後照護順序、加強環境稀釋漂白水清消。
  2. 純護理技術處置：
     - 呼吸道：協助半坐臥位、每2小時翻身拍背與姿位引流、適時抽痰維持呼吸道通暢、依護理評估給予低流量氧氣(O2 Cannula)支持、加強口腔清潔。
     - 泌尿道：落實無菌技術重置導尿管與密閉式引流袋、嚴格維持尿袋低於膀胱水平、加強會陰沖洗(CDO)、鼓勵適量飲水。
     - 腸胃道/皮膚：落實排泄物單獨清消、加強無菌傷口換藥與滲液評估、暫停共用物品。
  3. 床邊護理監測與機構內快篩：q4h 監測生命徵象（耳溫/BP/PR/RR/SpO2）、記錄 I/O 尿量與痰液/排泄物性狀；僅限機構內可自行操作之「尿液試紙(Dipstick)」或「COVID-19/流感抗原快篩」。
  4. 家屬聯繫與緊急送醫紅旗：記錄異常徵候通知家屬（或依機構流程聯繫特約門診），並明確列出啟動 119 緊急送醫之護理評估警訊（如 SpO2<90% 給氧未改善、呼吸急促 >30次/分、GCS 意識驟降、高燒寒顫伴隨血壓下降）。

【條件分流與精簡輸出規範】
請依據判定結果（is_case），嚴格依照以下條件填寫 JSON 欄位，文字務必精煉扼要：

- 若 is_case 為 true（確定收案）：
  1. `possible_causes`：精準列出「5 個」最可能感染原因（每點限 25 字以內，從管路照護、無菌技術、生理退化、嗆咳或交叉感染切入）。
  2. `nursing_measures`：條列純護理與照服端可執行措施（每點限 35 字以內，嚴格遵守上述禁用醫療處置鐵律）。
  3. `non_acceptance_reason`：填寫 `""`。

- 若 is_case 為 false（不收案）：
  1. `non_acceptance_reason`：條列具體「不收案原因」（每點限 30 字以內，指出欠缺條件、非感染性因素，或院外/住院潛伏期帶入）。
  2. `possible_causes`：填寫 `[]`。
  3. `nursing_measures`：填寫 `""`。

- 無論是否收案，皆須填寫 `missing_info_suggestions`（陣列，1~3 點，每點限 25 字以內）：
  指出「護理師可在床邊觀察或查閱護理紀錄補充的資訊」（例如：近期有無調整軟便劑、住民基礎耳溫、床邊聽診有無囉音、尿液試紙外觀/氣味等；嚴禁要求補充抽血或X光報告）。若資料已完整則回傳 `[]`。

【JSON 格式要求】
嚴格輸出純合法 JSON，結構如下：
{
  "is_case": true 或 false,
  "case_type": "呼吸道感染-肺炎" | "呼吸道感染-類流感" | "呼吸道感染-下呼吸道" | "呼吸道感染-感冒咽喉炎" | "泌尿道感染-有導尿管" | "泌尿道感染-無導尿管" | "腸胃道感染" | "皮膚軟組織感染" | "疥瘡" | "眼耳鼻口感染" | "血流感染" | "無法解釋發燒" | "不收案",
  "is_healthcare_associated": true 或 false,
  "detailed_reasoning": "簡明扼要總結時序審查與指引條款比對（限 60 字以內）。",
  "possible_causes": ["原因1", "原因2", "原因3", "原因4", "原因5"],
  "nursing_measures": "條列純護理端可執行的防護與照護措施（僅收案時填寫）",
  "non_acceptance_reason": "條列具體不收案原因（僅不收案時填寫）",
  "missing_info_suggestions": ["建議補充護理觀察1", "建議補充護理觀察2"]
}
"""

# 採用 gemini-3.8-flash 作為主力高精準度感管推理模型
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

# ==================== 輔助與防呆函式 ====================
def ensure_str(val):
    """防呆輔助函式：確保無論 AI 回傳字串或陣列，都能安全轉為純文字"""
    if isinstance(val, list):
        return "\n".join([str(item) for item in val])
    return str(val) if val is not None else ""

def call_gemini_fast(prompt_text, max_retries=2):
    for attempt in range(max_retries):
        try:
            return model.generate_content(
                prompt_text,
                generation_config={
                    "response_mime_type": "application/json",
                    "thinking_config": {"thinking_budget": 0}
                },
                request_options={"timeout": 60}
            )
        except Exception as e:
            err_msg = str(e)
            if "thinking_config" in err_msg or "Unknown field" in err_msg:
                return model.generate_content(
                    prompt_text,
                    generation_config={"response_mime_type": "application/json"},
                    request_options={"timeout": 60}
                )
            if "504" in err_msg and attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise e

def parse_ai_json(raw_text):
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
        if clean.startswith("json"):
            clean = clean[4:].strip()
    return json.loads(clean)

def build_log_row(payload, result, supplemental_text=""):
    is_case = result.get("is_case", False)
    causes = result.get("possible_causes", [])
    if not isinstance(causes, list):
        causes = [str(causes)] if causes else []
    
    full_note = payload["clinical_note"]
    if supplemental_text.strip():
        full_note += f"\n\n【二次補充資料 ({datetime.now().strftime('%H:%M')})】：{supplemental_text.strip()}"

    if is_case:
        reasoning_str = ensure_str(result.get("detailed_reasoning", ""))
        causes_str = "\n".join([str(c) for c in causes])
        sheet_reasoning = f"{reasoning_str}\n\n【可能感染原因】\n{causes_str}"
        sheet_actions = ensure_str(result.get("nursing_measures", ""))
    else:
        raw_non_acc = result.get("non_acceptance_reason") or result.get("detailed_reasoning", "")
        sheet_reasoning = ensure_str(raw_non_acc)
        sheet_actions = "不收案（維持常規照護與症狀觀察）"

    return [
        payload["timestamp"],
        payload["bed_num"],
        payload["name"],
        payload["catheter_status"],
        payload["has_recent_adm"],
        payload["onset_date"],
        payload["body_temp"],
        payload["symptoms_str"],
        full_note,
        "收案" if is_case else "不收案",
        ensure_str(result.get("case_type", "不收案")) if is_case else "不收案",
        "是" if result.get("is_healthcare_associated") else "否",
        sheet_reasoning,
        sheet_actions,
        "待複核"
    ]

# ==================== 護理站端通報介面 ====================
st.title("🏥 護理站感染管制通報與智慧收案系統")
st.caption("依據長期照護機構感染監測指引進行即時判定與雲端登錄（老人福利機構護理端專用）")

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
        placeholder="請詳細記錄臨床徵候、用藥異動（如是否剛吃軟便劑）、床邊快篩或尿液試紙結果等...",
        height=140
    )

submit_btn = st.button("送出並執行感管收案審核", type="primary", use_container_width=True)

# ==================== 3. 初次判定與存檔 ====================
if submit_btn:
    if not bed_num.strip() or not name.strip() or not clinical_note.strip():
        st.error("請務必完整填寫：床號、住民姓名以及護理病程經過！")
    else:
        progress_bar = st.progress(0)
        status_text = st.empty()

        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bed_num": bed_num.strip(),
            "name": name.strip(),
            "catheter_status": catheter_status,
            "has_recent_adm": has_recent_adm,
            "onset_date": str(onset_date),
            "body_temp": body_temp,
            "symptoms_str": ", ".join(symptoms) if symptoms else "未特別勾選，詳見病程",
            "clinical_note": clinical_note.strip()
        }

        prompt_content = f"""
        住民資訊：
        - 床號：{payload['bed_num']}
        - 姓名：{payload['name']}
        - 導尿管留置狀態：{payload['catheter_status']}
        - 近期出入院狀況：{payload['has_recent_adm']}
        - 症狀發生日：{payload['onset_date']}
        - 體溫：{payload['body_temp']} ℃
        - 勾選症狀：{payload['symptoms_str']}
        - 護理病程記錄：{payload['clinical_note']}
        """

        try:
            status_text.info("【1/3】感管專家引擎 (Gemini 3.8 Flash) 審核中...")
            progress_bar.progress(40)

            response = call_gemini_fast(prompt_content)

            status_text.info("【2/3】解析審核結果與護理觀察缺漏...")
            progress_bar.progress(80)

            result = parse_ai_json(response.text)

            st.session_state.audit_result = result
            st.session_state.base_payload = payload

            status_text.info("【3/3】同步寫入雲端試算表...")
            log_row = build_log_row(payload, result)
            sheet_logs.append_row(log_row)
            
            st.session_state.current_row_idx = len(sheet_logs.get_all_values())

            status_text.empty()
            progress_bar.empty()
            st.toast("✅ 初次審核完成並已存入 Google 試算表！")

        except json.JSONDecodeError:
            status_text.empty()
            progress_bar.empty()
            st.error("JSON 格式轉換失敗，模型原始輸出內容如下：")
            st.code(response.text)
        except Exception as e:
            status_text.empty()
            progress_bar.empty()
            st.error(f"執行或寫入試算表時發生錯誤：{e}")

# ==================== 4. 顯示判定結果與「二次補充資料重審區」 ====================
if st.session_state.audit_result is not None:
    result = st.session_state.audit_result
    is_case = result.get("is_case", False)

    st.divider()
    if is_case:
        st.error(f"🚨 【符合收案條件】感染分類：{ensure_str(result.get('case_type', '未分類'))}")
        st.markdown(f"**照護相關判定：** {'機構內感染' if result.get('is_healthcare_associated') else '非機構照護所致／院外帶入'}")
        st.markdown(f"**審核條款摘要：** {ensure_str(result.get('detailed_reasoning', ''))}")

        col_cause, col_care = st.columns(2, gap="medium")
        with col_cause:
            st.subheader("🔍 5 個可能感染原因")
            causes = result.get("possible_causes", [])
            if isinstance(causes, list) and causes:
                for idx, cause in enumerate(causes, 1):
                    st.markdown(f"**{idx}.** {cause}")
            elif causes:
                st.write(ensure_str(causes))
            else:
                st.write("無額外列出原因")

        with col_care:
            st.subheader("📋 機構純護理端可執行措施")
            st.warning(ensure_str(result.get("nursing_measures", "請依機構感管標準作業程序執行隔離與護理監測。")))

    else:
        st.info("📋 【未符合收案標準】判定結果：不收案")
        st.markdown(f"**照護相關判定：** {'機構內感染（未達定義門檻）' if result.get('is_healthcare_associated') else '非機構照護所致／院外帶入'}")
        
        st.subheader("❌ 不收案原因說明")
        non_acc_reason = ensure_str(result.get("non_acceptance_reason") or result.get("detailed_reasoning", ""))
        st.success(non_acc_reason)

    # ----- 互動補件回饋區 -----
    st.divider()
    st.subheader("💡 AI 感管員精準判決建議：可再補充之護理觀察")
    
    suggestions = result.get("missing_info_suggestions", [])
    if isinstance(suggestions, list) and suggestions:
        st.info("為讓本次收案判決更精準，建議護理站可進一步確認並補充以下項目：\n" + "\n".join([f"- **{s}**" for s in suggestions]))
    elif suggestions:
        st.info(f"建議補充項目：{ensure_str(suggestions)}")
    else:
        st.caption("目前提供的臨床資訊已相當完整。若仍有新進護理觀察或快篩結果，仍可於下方補充重審。")

    with st.form("supplemental_form"):
        supplemental_input = st.text_area(
            "補充護理觀察、床邊快篩或用藥異動紀錄（填寫後將同步更新至雲端資料庫並重新判定）",
            placeholder="例如：已核對給藥紀錄，近一週無調整軟便劑；剛執行床邊尿液試紙顯示 WBC 3+、Nitrite 陽性...",
            height=100
        )
        resubmit_btn = st.form_submit_button("🔄 寫入補充資料並重新進行感管判決", type="secondary", use_container_width=True)

    if resubmit_btn:
        if not supplemental_input.strip():
            st.warning("請先在上方欄位輸入要補充的資料再送出！")
        else:
            with st.spinner("正在結合補充資料進行二次精準審核，並同步更新資料庫..."):
                payload = st.session_state.base_payload
                combined_prompt = f"""
                住民資訊：
                - 床號：{payload['bed_num']}
                - 姓名：{payload['name']}
                - 導尿管留置狀態：{payload['catheter_status']}
                - 近期出入院狀況：{payload['has_recent_adm']}
                - 症狀發生日：{payload['onset_date']}
                - 體溫：{payload['body_temp']} ℃
                - 勾選症狀：{payload['symptoms_str']}
                - 初次護理病程記錄：{payload['clinical_note']}
                
                【護理站二次補充資料（請以此最新資訊為準重新判決）】：
                {supplemental_input.strip()}
                """
                try:
                    new_response = call_gemini_fast(combined_prompt)
                    new_result = parse_ai_json(new_response.text)

                    st.session_state.audit_result = new_result

                    updated_row = build_log_row(payload, new_result, supplemental_text=supplemental_input)
                    row_idx = st.session_state.current_row_idx
                    if row_idx:
                        sheet_logs.update(range_name=f"A{row_idx}:O{row_idx}", values=[updated_row])
                    else:
                        sheet_logs.append_row(updated_row)

                    st.toast("✅ 補充資料已同步覆寫至試算表，判決結果已更新！")
                    st.rerun()

                except Exception as e:
                    st.error(f"二次審核或更新資料庫時發生錯誤：{e}")
