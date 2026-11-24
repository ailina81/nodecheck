"""
NodeCheck — ИИ-аудит совместимости отделочных материалов.
Запуск:  streamlit run app.py
"""
import json
import os

import pandas as pd
import streamlit as st

from engine import (BUDGETS, ROOM_TYPES, SURFACES, UFH_TYPES, MATERIALS,
                    alternatives_for, audit, cheaper_analogs)
from report import build_pdf

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title="NodeCheck", page_icon="🧱", layout="wide")

# ---------------------------------------------------------------------------
# Примеры спецификаций (для демонстрации на защите)
# ---------------------------------------------------------------------------
EXAMPLES = {
    "— пустая спецификация —": (ROOM_TYPES[0], "Нет", []),
    "Квартира: ламинат на водяном тёплом полу": (ROOM_TYPES[0], "Водяной", [
        ("Пол", "Ламинат 33 класс"), ("Пол", "Ковролин в спальне"),
        ("Стены", "Моющаяся краска"), ("Потолок", "Натяжной потолок")]),
    "Санузел с ошибками": (ROOM_TYPES[1], "Электрический (кабельный в стяжке)", [
        ("Пол", "Ламинат"), ("Стены", "Гипсокартон ГКЛ"),
        ("Зона душа / прямой контакт с водой", "Моющаяся краска"),
        ("Потолок", "Бумажные обои")]),
    "Санузел правильный": (ROOM_TYPES[1], "Электрический (кабельный в стяжке)", [
        ("Пол", "Керамогранит"), ("Стены", "ГКЛВ"),
        ("Зона душа / прямой контакт с водой", "Плитка"),
        ("Клей / основание", "Обмазочная гидроизоляция"),
        ("Клей / основание", "Клей C2 S1"), ("Потолок", "Натяжной потолок")]),
    "Коридор общежития (путь эвакуации)": (ROOM_TYPES[3], "Нет", [
        ("Пол", "Бытовой линолеум"), ("Стены", "Виниловые обои"),
        ("Стены", "ПВХ панели"), ("Потолок", "Деревянная вагонка")]),
}

SYSTEM_PROMPT = """Ты — инженер-материаловед и эксперт строительной экспертизы Республики Казахстан
(СП РК, СНиП, технические регламенты по пожарной безопасности; при необходимости — аналоги РФ и EN).
Задача: проверить спецификацию отделочных материалов помещения на:
1) физико-технические конфликты (влагостойкость, термостойкость, тёплый пол, клеевые составы, основания);
2) соответствие нормам (пожарная безопасность отделки путей эвакуации — классы КМ0–КМ5, влажный режим, санитарные требования);
3) предложить безопасные альтернативы в том же стиле и бюджете;
4) если want_cheaper=true — для дорогих позиций предложить более дешёвые аналоги без потери технических свойств
   (их тоже клади в recommendations, в why укажи «дешевле»).

Тебе также передаются находки детерминированной базы правил — проверь их, исправь при необходимости и дополни.
Не выдумывай номера пунктов норм: если не уверен в пункте, указывай только название документа.
Пиши кратко, по-русски, для архитектора/дизайнера.

Верни СТРОГО JSON без markdown:
{
  "status": "ok" | "conflicts",
  "summary": "1–2 предложения общего вывода",
  "findings": [{"severity": "critical"|"warning", "item": "позиция спецификации",
                "title": "кратко", "explanation": "почему это проблема", "norm": "документ или пусто"}],
  "recommendations": [{"replace": "что заменить", "suggestion": "на что", "why": "почему, с учётом бюджета и стиля"}]
}"""


def llm_config():
    """Ключ берём из st.secrets, .env или поля в сайдбаре."""
    def get(name, default=""):
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:
            pass
        return os.getenv(name, default)
    return dict(api_key=get("LLM_API_KEY") or get("OPENAI_API_KEY"),
                base_url=get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/") or None,
                model=get("LLM_MODEL", "gemini-3.8-flash"))


def run_llm(cfg, room, ufh, budget, style, rows, rule_result, want_cheaper=False):
    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"] or None,
                    max_retries=5, timeout=60)  # автоповтор при 429/503 (перегрузка модели)
    user_msg = json.dumps(dict(
        room_type=room, underfloor_heating=ufh, budget=budget, style=style, want_cheaper=want_cheaper,
        specification=rows, rule_engine_findings=rule_result.to_dict()["findings"],
        unrecognized_items=rule_result.unknown), ensure_ascii=False, indent=1)
    resp = client.chat.completions.create(
        model=cfg["model"], temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user_msg}])
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Сайдбар: настройки ИИ
# ---------------------------------------------------------------------------
cfg = llm_config()
with st.sidebar:
    st.header("⚙️ Настройки ИИ")
    key_in = st.text_input("API-ключ (OpenAI / Gemini / совместимый)", value="",
                           type="password", placeholder="или задайте в .env")
    if key_in:
        cfg["api_key"] = key_in
    cfg["base_url"] = st.text_input("Base URL (по умолчанию Gemini; пусто = OpenAI)", value=cfg["base_url"] or "") or None
    cfg["model"] = st.text_input("Модель", value=cfg["model"])
    use_llm = st.toggle("Использовать LLM", value=bool(cfg["api_key"]))
    if use_llm and not cfg["api_key"]:
        st.warning("Ключ не задан — будет работать офлайн-база правил.")
    st.caption("Без ключа работает встроенная база правил — прототип всё равно выдаёт результат.")
    with st.expander("Какие материалы распознаёт база"):
        st.write("\n".join(f"- {m['name']}" for m in MATERIALS.values()))

# ---------------------------------------------------------------------------
# Основной экран
# ---------------------------------------------------------------------------
st.title("🧱 NodeCheck")
st.caption("ИИ-аудит совместимости отделочных материалов и соответствия нормам (СП РК / СНиП)")

left, right = st.columns([1, 1.15], gap="large")

with left:
    st.subheader("1. Исходные данные")
    ex = st.selectbox("Загрузить пример", list(EXAMPLES.keys()))
    ex_room, ex_ufh, ex_rows = EXAMPLES[ex]

    room = st.selectbox("Тип помещения", ROOM_TYPES, index=ROOM_TYPES.index(ex_room), key=f"room_{ex}")
    c1, c2 = st.columns(2)
    ufh = c1.selectbox("Тёплый пол", UFH_TYPES, index=UFH_TYPES.index(ex_ufh), key=f"ufh_{ex}")
    budget = c2.selectbox("Бюджет", BUDGETS, index=1)
    style = st.text_input("Стиль интерьера (для подбора замены)", "Современный минимализм")
    want_cheaper = st.checkbox("💰 Подобрать более дешёвые аналоги", value=True)

    st.markdown("**Спецификация материалов** — добавляйте строки, материал пишите свободно")
    df = pd.DataFrame(ex_rows or [("Пол", "")], columns=["Поверхность", "Материал"])
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", key=f"spec_{ex}",
        column_config={
            "Поверхность": st.column_config.SelectboxColumn(options=SURFACES, required=True),
            "Материал": st.column_config.TextColumn(width="large"),
        })
    go = st.button("🔍 Запустить ИИ-аудит совместимости", type="primary", width="stretch")

with right:
    st.subheader("2. Результат аудита")
    if not go:
        st.info("Заполните спецификацию слева и нажмите «Запустить ИИ-аудит».")
        st.stop()

    rows = [r for r in edited.fillna("").to_dict("records") if str(r.get("Материал", "")).strip()]
    if not rows:
        st.warning("Спецификация пустая.")
        st.stop()

    rule_res = audit(room, ufh, rows)
    data, mode = None, "Офлайн-база правил"
    if use_llm and cfg["api_key"]:
        with st.spinner("ИИ анализирует спецификацию…"):
            try:
                data = run_llm(cfg, room, ufh, budget, style, rows, rule_res, want_cheaper)
                mode = f"LLM ({cfg['model']}) + база правил"
            except Exception as e:
                st.warning(f"LLM недоступна ({type(e).__name__}: {e}). Показан результат базы правил.")

    if data is None:
        d = rule_res.to_dict()
        data = dict(status=d["status"], findings=d["findings"],
                    recommendations=alternatives_for(rule_res.findings, budget),
                    summary=("Конфликтов не найдено." if d["status"] == "ok" else
                             f"Найдено проблем: {len(d['findings'])}. Критичных: "
                             f"{sum(f['severity'] == 'critical' for f in d['findings'])}."))

    findings = data.get("findings", [])
    crit = [f for f in findings if f.get("severity") == "critical"]
    st.caption(f"Режим: {mode}")

    if not findings:
        st.success("🟢 Всё отлично — технических конфликтов не найдено")
    elif crit:
        st.error(f"🔴 Найдены технические конфликты: {len(crit)} критичных, {len(findings) - len(crit)} предупреждений")
    else:
        st.warning(f"🟡 Критичных ошибок нет, но есть предупреждения: {len(findings)}")
    if data.get("summary"):
        st.write(data["summary"])

    if findings:
        st.markdown("#### ❗ Найденные ошибки")
        for f in findings:
            icon = "🔴" if f.get("severity") == "critical" else "🟡"
            with st.container(border=True):
                st.markdown(f"{icon} **{f.get('title', '')}**")
                if f.get("item"):
                    st.caption(f.get("item"))
                st.write(f.get("explanation", ""))
                if f.get("norm"):
                    st.caption(f"📘 Норматив: {f['norm']}")

    recs = data.get("recommendations", [])
    if recs:
        st.markdown(f"#### ✅ Рекомендации (бюджет: {budget.lower()})")
        for r in recs:
            with st.container(border=True):
                st.markdown(f"**Заменить:** {r.get('replace', '')}  \n**На:** {r.get('suggestion', '')}")
                if r.get("why"):
                    st.caption(r["why"])

    cheaper = cheaper_analogs(room, ufh, rows) if want_cheaper else []
    if cheaper:
        st.markdown("#### 💰 Более дешёвые аналоги")
        for c in cheaper:
            with st.container(border=True):
                st.markdown(f"**{c['item']}** — сейчас: {c['current']}")
                st.write("Можно заменить на: " + " · ".join(c["options"]))
        st.caption("₸ — относительный уровень цены. Аналоги проходят те же проверки без критичных ошибок.")

    if rule_res.unknown and mode.startswith("Офлайн"):
        st.info("Не распознаны базой правил (подключите LLM для анализа):\n\n- " + "\n- ".join(rule_res.unknown))

    st.download_button("📄 Скачать PDF-отчёт",
                       build_pdf(room, ufh, budget, rows, data, cheaper, mode),
                       file_name="nodecheck_report.pdf", mime="application/pdf", type="primary")
    st.download_button("⬇️ Скачать отчёт (JSON)",
                       json.dumps(dict(room=room, ufh=ufh, budget=budget, spec=rows, result=data),
                                  ensure_ascii=False, indent=2),
                       file_name="nodecheck_report.json", mime="application/json")
    st.caption("⚠️ Прототип. Результаты носят справочный характер — сверяйте с актуальной редакцией норм.")
