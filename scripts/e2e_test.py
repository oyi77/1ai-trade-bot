import json
import sys
import urllib.request

BASE_URL = "http://localhost:8889"


def test_endpoint(path, expected_keys=None):
    url = f"{BASE_URL}{path}"
    print(f"Testing {url} ... ", end="")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "E2ETester/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
            if status != 200:
                print(f"FAILED (HTTP {status})")
                return False

            body = r.read().decode("utf-8")
            if expected_keys:
                try:
                    data = json.loads(body)
                    for key in expected_keys:
                        if key not in data:
                            print(f"FAILED (Missing key '{key}')")
                            return False
                except json.JSONDecodeError:
                    print("FAILED (Invalid JSON)")
                    return False

            print("PASSED")
            return True
    except Exception as e:
        print(f"FAILED (Exception: {e})")
        return False


def run_tests():
    success = True

    # 1. Test Web login page
    if not test_endpoint("/login"):
        success = False

    # 2. Test public APIs
    if not test_endpoint("/api/feed/stats", ["total", "tp", "sl", "pending"]):
        success = False

    # 3. Test Monitoring APIs
    if not test_endpoint(
        "/api/monitoring/status", ["uptime_seconds", "engines", "brokers", "metrics"]
    ):
        success = False
    if not test_endpoint("/api/monitoring/metrics", ["signals", "trades", "errors"]):
        success = False
    if not test_endpoint("/api/monitoring/trades/live", ["trades", "count"]):
        success = False

    if success:
        print("\n🎉 ALL WEB AND API END-TO-END TESTS PASSED SUCCESSFULLY! 🎉")
        sys.exit(0)
    else:
        print("\n❌ SOME END-TO-END TESTS FAILED! ❌")
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
