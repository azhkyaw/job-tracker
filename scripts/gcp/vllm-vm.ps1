# The vLLM lab VM — PowerShell twin of vllm-vm.sh (docs/vllm-lab.md §3), for
# the author's Windows Terminal. Same knobs, same choices; the .sh carries the
# reasoning next to each flag, this file only carries the shell. Commands run
# ON the VM (serve, logs) are Linux and stay bash whatever the local shell.
#
#   .\scripts\gcp\vllm-vm.ps1 create | serve | logs | tunnel | start | stop | status | ssh | delete
#   $env:VLLM_API_KEY = '...' ; .\scripts\gcp\vllm-vm.ps1 serve
#
# Windows PowerShell 5.1: gcloud.ps1 writes progress to stderr, which shows
# red and is harmless — hence 'Continue', not 'Stop'.
param([Parameter(Position = 0)][string]$Cmd = '')
$ErrorActionPreference = 'Continue'

function Default($value, $fallback) { if ($value) { $value } else { $fallback } }
$Project   = Default $env:VLLM_PROJECT       'vllm-lab-2609'
$Zone      = Default $env:VLLM_ZONE          'asia-southeast1-b'
$Name      = Default $env:VLLM_VM            'vllm-1'
$Machine   = Default $env:VLLM_MACHINE       'g2-standard-4'
$Image     = Default $env:VLLM_IMAGE_FAMILY  'common-cu129-ubuntu-2404-nvidia-580'
$Model     = Default $env:VLLM_MODEL         'Qwen/Qwen3-8B'
$MaxLen    = Default $env:VLLM_MAX_MODEL_LEN '16384'
$LocalPort = Default $env:VLLM_LOCAL_PORT    '8001'
# Single quotes around the JSON survive to the VM's bash; without them it
# strips the double quotes and vLLM rejects {enable_thinking:false}.
$ExtraArgs = Default $env:VLLM_EXTRA_ARGS "--reasoning-parser qwen3 --default-chat-template-kwargs '{""enable_thinking"":false}'"
$ApiKey    = $env:VLLM_API_KEY

function vm { param([string]$Verb, [string[]]$Rest = @())
    gcloud compute instances $Verb $Name --project $Project --zone $Zone @Rest }
# ssh is `gcloud compute ssh`, not an `instances` verb — found by running it.
function sshvm { param([string[]]$Rest = @())
    gcloud compute ssh $Name --project $Project --zone $Zone --tunnel-through-iap @Rest }

switch ($Cmd) {
    'create' {
        vm create @('--machine-type', $Machine,
                    '--accelerator', 'type=nvidia-l4,count=1',
                    '--provisioning-model=SPOT', '--instance-termination-action=STOP',
                    '--maintenance-policy=TERMINATE',
                    '--image-family', $Image, '--image-project', 'deeplearning-platform-release',
                    '--boot-disk-size', '200GB', '--boot-disk-type', 'pd-balanced',
                    '--metadata', 'install-nvidia-driver=True',
                    '--scopes', 'cloud-platform', '--tags', 'vllm')
        gcloud compute firewall-rules describe vllm-iap-8000 --project $Project *> $null
        if ($LASTEXITCODE -ne 0) {
            gcloud compute firewall-rules create vllm-iap-8000 --project $Project --network default `
                --direction INGRESS --source-ranges 35.235.240.0/20 --allow tcp:8000 --target-tags vllm
        }
        "created. Wait ~2 min for the driver install, then: $PSCommandPath serve"
    }
    { $_ -in 'start', 'stop', 'delete' } { vm $Cmd }
    'status' { vm describe @('--format=value(status,networkInterfaces[0].networkIP,scheduling.provisioningModel)') }
    'ssh'    { sshvm }
    'serve' {
        if (-not $ApiKey) { Write-Error 'set $env:VLLM_API_KEY (the bearer the tracker will send)'; exit 1 }
        # The remote script is base64'd through ssh so no quoting has to
        # survive PowerShell, plink and bash in turn. `$HOME is escaped so the
        # VM expands it, not this shell.
        $remote = @"
set -e
mkdir -p "`$HOME/.cache/huggingface"
docker rm -f vllm >/dev/null 2>&1 || true
docker run -d --name vllm --restart unless-stopped --gpus all --ipc=host -p 8000:8000 \
  -v "`$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -e VLLM_API_KEY='$ApiKey' \
  vllm/vllm-openai:latest \
  --model '$Model' --max-model-len $MaxLen --gpu-memory-utilization 0.90 \
  $ExtraArgs
echo "started: docker logs -f vllm"
"@
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($remote -replace "`r`n", "`n")))
        sshvm @('--command', "echo $b64 | base64 -d > /tmp/serve.sh && bash /tmp/serve.sh")
    }
    'logs'   { sshvm @('--command', 'docker logs -f --tail 200 vllm') }
    'tunnel' {
        "localhost:$LocalPort -> ${Name}:8000 (Ctrl-C to close)"
        gcloud compute start-iap-tunnel $Name 8000 --local-host-port="localhost:$LocalPort" --zone $Zone --project $Project
    }
    default  { Get-Content $PSCommandPath -TotalCount 7; exit 1 }
}
