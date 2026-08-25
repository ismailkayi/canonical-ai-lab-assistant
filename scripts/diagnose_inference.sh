#!/usr/bin/env bash
# Inference snap tanı aracı - Tüm olası sorunları kontrol eder

set -u

INFERENCE_HOST="${INFERENCE_HOST:-http://127.0.0.1:8336}"
INFERENCE_MODEL="${INFERENCE_MODEL:-gemma4}"
COLORS_ENABLED=true

# Color functions
color_green() {
    if [[ "${COLORS_ENABLED}" == "true" ]]; then
        echo -e "\033[0;32m✓\033[0m $1"
    else
        echo "[OK] $1"
    fi
}

color_red() {
    if [[ "${COLORS_ENABLED}" == "true" ]]; then
        echo -e "\033[0;31m✗\033[0m $1"
    else
        echo "[FAIL] $1"
    fi
}

color_yellow() {
    if [[ "${COLORS_ENABLED}" == "true" ]]; then
        echo -e "\033[0;33m⚠\033[0m $1"
    else
        echo "[WARN] $1"
    fi
}

color_blue() {
    if [[ "${COLORS_ENABLED}" == "true" ]]; then
        echo -e "\033[0;34mℹ\033[0m $1"
    else
        echo "[INFO] $1"
    fi
}

echo "============================================================"
echo "Inference Snap Tanı Aracı"
echo "============================================================"
echo ""
echo "Configured INFERENCE_HOST: $INFERENCE_HOST"
echo "Configured INFERENCE_MODEL: $INFERENCE_MODEL"
echo ""

# Test 1: Snap yüklü mü?
echo "--- TEST 1: Snap Kurulumu ---"
if snap list gemma4 >/dev/null 2>&1; then
    color_green "gemma4 snap kurulu"
    snap info gemma4 | head -10
else
    color_red "gemma4 snap bulunamadı"
    echo "Çözüm: sudo snap install gemma4"
    exit 1
fi
echo ""

# Test 2: Snap servisleri çalışıyor mu?
echo "--- TEST 2: Snap Servisleri ---"
if snap services gemma4 2>/dev/null | grep -q "active"; then
    color_green "Snap servisleri aktif"
    snap services gemma4
else
    color_red "Snap servisleri aktif değil"
    echo "Çözüm: sudo snap restart gemma4"
fi
echo ""

# Test 3: Port açık mı?
echo "--- TEST 3: Port Kontrolü ---"
PORT=$(echo "$INFERENCE_HOST" | grep -oP ':\K[0-9]+' || echo "8336")
HOST_ADDR=$(echo "$INFERENCE_HOST" | sed 's|http://||' | sed 's/:.*//') || HOST_ADDR="127.0.0.1"

if command -v ss &>/dev/null; then
    if ss -tulpn 2>/dev/null | grep -q ":$PORT"; then
        color_green "Port $PORT dinleniyor"
        ss -tulpn 2>/dev/null | grep ":$PORT"
    else
        color_red "Port $PORT dinlenmiyor"
    fi
elif command -v netstat &>/dev/null; then
    if netstat -tulpn 2>/dev/null | grep -q ":$PORT"; then
        color_green "Port $PORT dinleniyor"
        netstat -tulpn 2>/dev/null | grep ":$PORT"
    else
        color_red "Port $PORT dinlenmiyor"
    fi
else
    color_yellow "ss/netstat bulunamadı, port kontrolü atlanıyor"
fi
echo ""

# Test 4: Health endpoint
echo "--- TEST 4: Health Endpoint ---"
HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" "$INFERENCE_HOST/health" 2>/dev/null | tail -1)
if [[ "$HEALTH_RESPONSE" == "200" ]]; then
    color_green "Health endpoint yanıt veriyor (HTTP 200)"
    curl -s "$INFERENCE_HOST/health" | jq . 2>/dev/null || echo "  Response: $HEALTH_RESPONSE"
else
    color_red "Health endpoint yanıt vermiyor (HTTP $HEALTH_RESPONSE)"
    echo "  Deneme: curl -v $INFERENCE_HOST/health"
fi
echo ""

# Test 5: Modeller
echo "--- TEST 5: Mevcut Modeller ---"
MODELS_RESPONSE=$(curl -s -w "\n%{http_code}" "$INFERENCE_HOST/v1/models" 2>/dev/null | tail -1)
if [[ "$MODELS_RESPONSE" == "200" ]]; then
    color_green "Modeller endpoint yanıt veriyor"
    MODELS=$(curl -s "$INFERENCE_HOST/v1/models" 2>/dev/null | jq -r '.data[].id // .models[].name // .models[].model' 2>/dev/null | head -10)
    if [[ -z "$MODELS" ]]; then
        color_yellow "Model listesi boş"
    else
        echo "  Mevcut modeller:"
        echo "$MODELS" | sed 's/^/    - /'
        
        # Konfigüre edilen model var mı?
        if echo "$MODELS" | grep -q "$INFERENCE_MODEL"; then
            color_green "Konfigüre edilen model bulundu: $INFERENCE_MODEL"
        elif echo "$MODELS" | grep -q "gemma4"; then
            MODEL_NAME=$(echo "$MODELS" | grep "gemma4" | head -1)
            color_yellow "Konfigüre edilen model '$INFERENCE_MODEL' bulunamadı, ancak bu model var: $MODEL_NAME"
            echo "  Çözüm: export INFERENCE_MODEL=$MODEL_NAME"
        else
            color_yellow "Konfigüre edilen model bulunamadı"
        fi
    fi
else
    color_red "Modeller endpoint yanıt vermiyor (HTTP $MODELS_RESPONSE)"
fi
echo ""

# Test 6: Chat endpoint
echo "--- TEST 6: Chat Endpoint ---"
CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$INFERENCE_HOST/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model":"gemma4-e4b-q4-k-m",
        "messages":[{"role":"user","content":"test"}],
        "stream":false,
        "max_tokens":10
    }' 2>/dev/null | tail -1)

if [[ "$CHAT_RESPONSE" == "200" ]]; then
    color_green "Chat endpoint yanıt veriyor (HTTP 200)"
else
    color_red "Chat endpoint yanıt vermiyor (HTTP $CHAT_RESPONSE)"
fi
echo ""

# Test 7: Python client test
echo "--- TEST 7: Python Client Test ---"
if command -v python3 &>/dev/null; then
    PYTHON_TEST=$(python3 << 'PYEOF'
import sys
sys.path.insert(0, '.')
try:
    from lab_ai_assistant.config import get_config
    from lab_ai_assistant.ai_engine import AIEngine
    config = get_config()
    ai = AIEngine(config)
    if ai.is_available():
        print("PASS")
    else:
        print("FAIL")
except Exception as e:
    print(f"ERROR: {e}")
PYEOF
)
    
    case "$PYTHON_TEST" in
        "PASS")
            color_green "Python client test başarılı"
            ;;
        "FAIL")
            color_red "Python client test başarısız"
            echo "  Çalışmakta olan işlemi kontrol edin veya INFERENCE_HOST'u ayarlayın"
            ;;
        *)
            color_yellow "Python client test: $PYTHON_TEST"
            ;;
    esac
else
    color_yellow "Python3 bulunamadı, Python client test atlanıyor"
fi
echo ""

# Özet
echo "============================================================"
echo "Tanı Özeti"
echo "============================================================"
echo ""
echo "Eğer tüm testler geçmiş ise:"
echo "  source .venv/bin/activate && lab-ai chat"
echo ""
echo "Eğer hala sorun varsa:"
echo "  export INFERENCE_HOST=http://<real_host_ip>:8336"
echo "  export INFERENCE_MODEL=<actual_model_name>"
echo "  lab-ai check"
echo ""
echo "Detaylı log görmek için:"
echo "  export LOG_LEVEL=DEBUG"
echo "  lab-ai --debug check"
echo ""
