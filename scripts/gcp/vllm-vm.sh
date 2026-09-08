#!/usr/bin/env bash
# The vLLM lab VM (docs/vllm-lab.md §3): one NVIDIA L4 on a Spot g2-standard-4
# in Singapore, Google's Deep Learning VM image (driver + Docker preinstalled),
# the official vllm/vllm-openai container. Nothing is reachable from the
# internet: no firewall rule opens port 8000 to the world, only to Google's
# IAP range, and you reach the server through `tunnel`, so the tracker's
# config is simply TRACKER_LLM_BASE_URL=http://127.0.0.1:8001/v1.
#
#   scripts/gcp/vllm-vm.sh create            # once (needs L4 quota — §2)
#   VLLM_API_KEY=... scripts/gcp/vllm-vm.sh serve   # (re)start the container
#   scripts/gcp/vllm-vm.sh logs              # watch weights load, KV cache sizing
#   scripts/gcp/vllm-vm.sh tunnel            # keep open; localhost:8001 -> VM:8000
#   scripts/gcp/vllm-vm.sh stop              # ALWAYS, when done — the GPU bills by the second
#   scripts/gcp/vllm-vm.sh start | ssh | status | delete
#
# Every knob is an env var so a different model or machine is a one-line change:
set -euo pipefail

PROJECT="${VLLM_PROJECT:-vllm-lab-2609}"
ZONE="${VLLM_ZONE:-asia-southeast1-b}"
NAME="${VLLM_VM:-vllm-1}"
MACHINE="${VLLM_MACHINE:-g2-standard-4}"          # 1x L4 24 GB; g2-standard-24 = 2x L4
IMAGE_FAMILY="${VLLM_IMAGE_FAMILY:-common-cu129-ubuntu-2404-nvidia-580}"
MODEL="${VLLM_MODEL:-Qwen/Qwen3-8B}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-16384}"       # classify ~3.8k tokens in, extract up to ~12k
LOCAL_PORT="${VLLM_LOCAL_PORT:-8001}"             # 8000 is the tracker's own UI
# Qwen3 is a hybrid thinking model: parse the reasoning out of the answer, and
# turn it off by default — these are short extraction tasks and max_tokens
# caps reasoning plus answer together. Override wholesale with VLLM_EXTRA_ARGS.
# The JSON keeps its single quotes all the way to the VM: the remote shell
# strips a bare {"…"} to {…} and vLLM then rejects it as not JSON.
EXTRA_ARGS="${VLLM_EXTRA_ARGS:---reasoning-parser qwen3 --default-chat-template-kwargs '{\"enable_thinking\":false}'}"
API_KEY="${VLLM_API_KEY:-}"

gc() { gcloud --project "$PROJECT" "$@"; }
vm() { gc compute instances "$1" "$NAME" --zone "$ZONE" "${@:2}"; }
# ssh is `gcloud compute ssh`, not an `instances` verb — found by running it.
sshvm() { gc compute ssh "$NAME" --zone "$ZONE" --tunnel-through-iap "$@"; }

case "${1:-}" in
  create)
    # Spot: a fraction of on-demand, may be preempted (then it STOPS, keeping
    # the disk and the downloaded weights). 200 GB so several models fit.
    vm create \
      --machine-type "$MACHINE" \
      --accelerator "type=nvidia-l4,count=1" \
      --provisioning-model=SPOT --instance-termination-action=STOP \
      --maintenance-policy=TERMINATE \
      --image-family "$IMAGE_FAMILY" --image-project deeplearning-platform-release \
      --boot-disk-size 200GB --boot-disk-type pd-balanced \
      --metadata install-nvidia-driver=True \
      --scopes cloud-platform --tags vllm
    # IAP TCP forwarding reaches the VM from 35.235.240.0/20 only. This is the
    # ONLY rule that opens 8000, and only to that range.
    gc compute firewall-rules describe vllm-iap-8000 >/dev/null 2>&1 || \
      gc compute firewall-rules create vllm-iap-8000 --network default \
        --direction INGRESS --source-ranges 35.235.240.0/20 --allow tcp:8000 \
        --target-tags vllm
    echo "created. Wait ~2 min for the driver install, then: $0 serve"
    ;;
  start|stop|delete)
    vm "$1" ;;
  status)
    vm describe --format="value(status,networkInterfaces[0].networkIP,scheduling.provisioningModel)" ;;
  ssh)
    sshvm ;;
  serve)
    [ -n "$API_KEY" ] || { echo "set VLLM_API_KEY (the bearer the tracker will send)" >&2; exit 1; }
    # The remote script is base64'd through ssh so no quoting survives two
    # shells and a JSON argument — the dead simplest thing that works.
    REMOTE=$(base64 -w0 <<EOF
set -e
mkdir -p "\$HOME/.cache/huggingface"
docker rm -f vllm >/dev/null 2>&1 || true
docker run -d --name vllm --restart unless-stopped --gpus all --ipc=host -p 8000:8000 \
  -v "\$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -e VLLM_API_KEY='$API_KEY' \
  vllm/vllm-openai:latest \
  --model '$MODEL' --max-model-len $MAX_MODEL_LEN --gpu-memory-utilization 0.90 \
  $EXTRA_ARGS
echo "started: docker logs -f vllm"
EOF
)
    sshvm --command "echo $REMOTE | base64 -d > /tmp/serve.sh && bash /tmp/serve.sh" ;;
  logs)
    sshvm --command "docker logs -f --tail 200 vllm" ;;
  tunnel)
    echo "localhost:$LOCAL_PORT -> $NAME:8000 (Ctrl-C to close)"
    gc compute start-iap-tunnel "$NAME" 8000 --local-host-port="localhost:$LOCAL_PORT" --zone "$ZONE" ;;
  *)
    sed -n '2,15p' "$0"; exit 1 ;;
esac
