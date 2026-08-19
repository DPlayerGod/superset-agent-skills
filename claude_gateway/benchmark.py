import json
import uuid
import time
import requests

# 127.0.0.1, not "localhost": the IPv6-first path through Docker Desktop on Windows
# adds ~21s per request, which lands in the measured latency.
URL = "http://127.0.0.1:8090/api/v1/agent/query"

QUESTIONS = [
    "Tổng số lượng nhân viên hiện tại là bao nhiêu?",
    "Vẽ biểu đồ số lượng nhân viên theo dự án",
    "Kiểm tra kết nối và sức khỏe hệ thống",
    "Tìm các bảng có trong public schema",
    "Liệt kê tất cả các dashboard hiện có"
]

def run_test(variant):
    print(f"\n=== Running {variant} variant ===")
    results = []
    for idx, q in enumerate(QUESTIONS):
        session_id = str(uuid.uuid4())
        print(f"[{idx+1}/{len(QUESTIONS)}] Question: {q}")
        t0 = time.time()
        try:
            r = requests.post(
                URL,
                json={
                    "question": q,
                    "session_id": session_id,
                    "context": {},
                    "row_limit": 200,
                    "variant": variant
                },
                timeout=120
            )
            elapsed = time.time() - t0
            if r.status_code == 200:
                data = r.json()
                timing = data.get("timing", {})
                total_ms = timing.get("total_ms", int(elapsed * 1000))
                tool_calls = timing.get("tool_calls", 0)
                print(f" -> Success! Latency: {total_ms/1000:.2f}s, Tool Calls: {tool_calls}")
                results.append({"question": q, "latency_s": total_ms / 1000, "tool_calls": tool_calls, "success": True})
            else:
                print(f" -> Failed with status code: {r.status_code}. Response: {r.text}")
                results.append({"question": q, "latency_s": elapsed, "tool_calls": 0, "success": False})
        except Exception as e:
            elapsed = time.time() - t0
            print(f" -> Exception occurred: {e}")
            results.append({"question": q, "latency_s": elapsed, "tool_calls": 0, "success": False})
    return results

if __name__ == "__main__":
    baseline_res = run_test("baseline")
    skills_res = run_test("skills")
    
    print("\n================ SUMMARY ================")
    for var, res in [("baseline", baseline_res), ("skills", skills_res)]:
        success_runs = [r for r in res if r["success"]]
        if not success_runs:
            print(f"{var}: No successful runs")
            continue
        avg_latency = sum(r["latency_s"] for r in success_runs) / len(success_runs)
        avg_tools = sum(r["tool_calls"] for r in success_runs) / len(success_runs)
        print(f"{var.upper()}:")
        print(f"  Avg Latency: {avg_latency:.2f}s")
        print(f"  Avg Tool Calls: {avg_tools:.2f}")
        print(f"  Success rate: {len(success_runs)}/{len(res)}")
