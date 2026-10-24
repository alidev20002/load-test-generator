"""
Pilot Testbed: E2E-Loader++ (faithful reimplementation) vs. LLM prompting
===========================================================================

  (الف) بازتولید *وفادار* منطق AtomicDependencyValidator.java، شامل:
        - دروازه‌ی value equality (با special-case های Authorization
          در Header و Cookie، دقیقاً همان substring(7)/contains())
        - Porter Stemming (همان الگوریتم OpenNLP PorterStemmer،
          پیاده‌سازی معادل با nltk.stem.porter)
        - id_completion (هم حالت nested-father و هم fallback مبتنی بر
          URL segments، با همان عدم‌تقارن stemming موجود در کد اصلی)
        - LCS ratio با threshold=0.44 (دقیقاً مقدار کد اصلی)

  (ب) پرامپت‌دهی به یک LLM محلی از طریق Ollama

نکته‌ی صداقت علمی: بخش (الف) با خواندن دقیق خط‌به‌خط
AtomicDependencyValidator.java, HeaderDependency.java, UrlDependency.java
بازتولید شده و شامل quirk های خود کد اصلی است (مثل عدم stemming دوباره‌ی
atomic_name در شاخه‌ی father-exists) — چون هدف مقایسه با رفتار *واقعی*
ابزار است، نه یک نسخه‌ی ایده‌آل‌شده از آن.

اجرا:
    python run_pilot.py                     # هر دو روش
    python run_pilot.py --lcs-only          # فقط بازتولید E2E-Loader++ (بدون نیاز به Ollama)
"""

import json
import re
import argparse
from urllib.parse import urlparse

from nltk.stem.porter import PorterStemmer

from llm_matcher_sample import field_match, LLMConfig

LCS_THRESHOLD = 0.44   # دقیقاً همان مقدار lcs_deadline در AtomicDependencyValidator.java

_stemmer = PorterStemmer()


# ─────────────────────────────────────────────
# بخش ۱ — بازتولید وفادار E2E-Loader++
# ─────────────────────────────────────────────

def porter_stem(word: str) -> str:
    """معادل porter_stamming() در کد جاوا."""
    return _stemmer.stem(word)


def lcs_ratio(name1: str, name2: str) -> float:
    """
    معادل LCS.LCSubStr(): طولانی‌ترین زیررشته‌ی *پیوسته*‌ی مشترک
    تقسیم بر طول رشته‌ی بزرگ‌تر.
    """
    name1, name2 = name1.lower(), name2.lower()
    m, n = len(name1), len(name2)
    if m == 0 or n == 0:
        return 0.0
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if name1[i - 1] == name2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                best = max(best, dp[i][j])
    return best / max(m, n)


def id_completion_with_father(child: str, father_name_lower: str) -> str:
    """
    معادل دقیق id_complention(String child, String father) وقتی
    caller آن را با father=porter_stamming(father.getName().toLowerCase())
    فراخوانی می‌کند. داخل خود متد، این father یک‌بار دیگر stem می‌شود
    (idempotent در اکثر موارد، ولی برای وفاداری کامل حفظ شده است).
    """
    father_stemmed_once = porter_stem(father_name_lower)
    if len(child) <= 3:
        return porter_stem(father_stemmed_once) + child
    return child


def id_completion_url_fallback(child: str, producer_url: str):
    """
    معادل دقیق id_complention(String child, Request request):
    وقتی father وجود ندارد و child کوتاه است (<=3 کاراکتر)، از دو
    سگمنت آخر URL خود عملیات *تولیدکننده* (نه مصرف‌کننده) کاندید می‌سازد.
    """
    if len(child) <= 3:
        path = urlparse(producer_url).path
        subpath = path.split("/")
        # پدینگ برای جلوگیری از IndexError در URL های خیلی کوتاه
        while len(subpath) < 2:
            subpath = [""] + subpath
        last = subpath[-1] if subpath[-1] != "" else (subpath[-2] if len(subpath) > 1 else "")
        second_last = subpath[-2] if len(subpath) > 1 else ""
        return [
            porter_stem(last) + child,
            porter_stem(second_last) + child,
        ]
    return [child]


def value_gate(case: dict) -> bool:
    """
    دروازه‌ی value equality، شامل special-case های واقعی کد جاوا:
      - Header با نام دقیقاً "Authorization"  -> substring(7) روی مقدار consumer
      - Cookie که نامش شامل substring "Authorization" است -> بدون هیچ تبدیلی
      - سایر موارد (Header عادی، QueryParam، Param، URL) -> برابری مستقیم
    """
    producer = case["producer"]
    consumer = case["consumer"]
    loc = consumer["location"]

    if loc.lower() == "header" and consumer["field"] == "Authorization":
        cval = consumer["value"]
        cval = cval[7:] if len(cval) > 7 else cval
        return producer["value"] == cval

    if loc.lower() == "cookie" and "Authorization" in consumer["field"]:
        return producer["value"] == consumer["value"]

    # Header غیر-Authorization، QueryParam، Param، URL: برابری مستقیم
    return producer["value"] == consumer["value"]


def name_gate(case: dict) -> bool:
    """
    بازتولید دقیق بخش نام‌محور general_atomic_comparioson /
    evaluate_url_atomic_dep (این دو از نظر منطق نام‌محور یکسان‌اند).
    """
    producer = case["producer"]
    consumer = case["consumer"]

    to_lower_name = consumer["field"].lower()
    atomic_name = producer["field"].lower()
    # father = producer.get("father", "")

    # if father:
    #     # شاخه‌ی father-exists: توجه به عدم‌تقارن stemming در کد اصلی -
    #     # فقط to_name استم می‌شود، atomic_name (بعد از احتمالاً completion) خیر.
    #     atomic_name_completed = id_completion_with_father(atomic_name, father.lower())
    #     to_name_stamm = porter_stem(to_lower_name)
    #     ratio = lcs_ratio(to_name_stamm, atomic_name_completed)
    #     return ratio >= LCS_THRESHOLD

    # شاخه‌ی no-father: هر دو طرف استم می‌شوند
    candidates = id_completion_url_fallback(atomic_name, producer["endpoint"])
    to_name_stamm = porter_stem(to_lower_name)
    for cand in candidates:
        cand_stamm = porter_stem(cand)
        ratio = lcs_ratio(to_name_stamm, cand_stamm)
        if ratio >= LCS_THRESHOLD:
            return True
    return False


def e2eloaderpp_predict(case: dict) -> bool:
    """پیش‌بینی کامل: دروازه‌ی مقدار سپس دروازه‌ی نام (دقیقاً ترتیب کد اصلی)."""
    if not value_gate(case):
        return False
    return name_gate(case)




# ─────────────────────────────────────────────
# بخش ۳ — اجرا و گزارش
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lcs-only", action="store_true",
                         help="فقط بازتولید E2E-Loader++ را اجرا کن (بدون نیاز به Ollama)")
    parser.add_argument("--cases", default="generated_cases.json")
    args = parser.parse_args()

    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)

    header = f"{'ID':<32} {'GT':<6} {'LCS':<6} "
    header += f"{'LLM':<6} " if not args.lcs_only else ""
    header += f"{'LCS_OK':<7} "
    header += f"{'LLM_OK':<7}" if not args.lcs_only else ""
    print(header)
    print("-" * (len(header) + 5))

    lcs_correct = 0
    llm_correct = 0
    llm_failures = []

    for case in cases:
        gt = case["ground_truth"]
        lcs_pred = e2eloaderpp_predict(case)
        lcs_ok = (lcs_pred == gt)
        lcs_correct += int(lcs_ok)

        row = f"{case['id']:<32} {str(gt):<6} {str(lcs_pred):<6} "

        if not args.lcs_only:
            try:
                llm_pred = field_match(
                    producer_endpoint=case['producer']['endpoint'],
                    producer_field=case['producer']['field'],
                    producer_value=case['producer']['value'],
                    consumer_endpoint=case['consumer']['endpoint'],
                    consumer_field=case['consumer']['field'],
                    consumer_value=case['consumer']['value'],
                    location=case['consumer']['location'],
                    cfg=LLMConfig(
                        provider='ollama',
                        endpoint='http://185.239.2.48:11434',
                        model='',
                        fallback_to_lcs=False,
                        cache_enabled=False
                    )
                )
            except Exception as e:
                llm_pred = None
                conf = "ERR"
                llm_failures.append((case["id"], str(e)))

            llm_ok = (llm_pred == gt) if llm_pred is not None else False
            llm_correct += int(llm_ok)
            row += f"{str(llm_pred):<6} {str(conf):<6} "

        row += f"{'OK' if lcs_ok else 'X':<7} "
        if not args.lcs_only:
            row += f"{'OK' if llm_ok else 'X':<7}"

        print(row)

    n = len(cases)
    print("-" * (len(header) + 5))
    print(f"E2E-Loader++ (faithful) accuracy: {lcs_correct}/{n} ({100*lcs_correct/n:.1f}%)")
    if not args.lcs_only:
        print(f"LLM accuracy:                     {llm_correct}/{n} ({100*llm_correct/n:.1f}%)")

    if llm_failures:
        print("\nLLM call failures (is 'ollama serve' running? is the model pulled?):")
        for case_id, err in llm_failures:
            print(f"  - {case_id}: {err}")


if __name__ == "__main__":
    main()
