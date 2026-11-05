# Run ONCE in your repo root (D:\thesis-swarm), BEFORE you copy in the zip contents.
# It moves the files with git mv (so history follows them) and removes what is no longer used.
# Your scenarios\, data\, model.txt and prompt texts are moved or left alone, never overwritten.
$ErrorActionPreference = "Continue"

foreach ($d in "swarm\core","swarm\llm_agents","swarm\code_agents","swarm\output","prompts","tests","docs") {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# core
foreach ($f in "state_machine","handoff","schemas","worker_schemas","requirements_schema","soul_loader","version_info","deps") {
    git mv "$f.py" "swarm\core\$f.py"
}
# plain-code agents
foreach ($f in "audit","plausibility","reality","plan_quality","dispatcher","finance","local_logistics","practical","route_order","static_web") {
    git mv "$f.py" "swarm\code_agents\$f.py"
}
# model-driven agent (Justice keeps its file; the others are new files from the zip)
git mv justice.py swarm\llm_agents\justice.py
git mv output_writer.py swarm\output\output_writer.py
git mv batch_summary.py swarm\output\batch_summary.py
git mv skills swarm\skills

# prompts: agents\<name>\SOUL.md -> prompts\<name>\SOUL.md
foreach ($n in "tech_planner","logistics","justice","transit","outreach") {
    New-Item -ItemType Directory -Force -Path "prompts\$n" | Out-Null
    git mv "agents\$n\SOUL.md" "prompts\$n\SOUL.md"
}

# tests and docs
Get-ChildItem -Filter "test_*.py" | ForEach-Object { git mv $_.Name "tests\$($_.Name)" }
foreach ($f in "ARCHITECTURE.md","CODE_TOUR.md","THIRD_PARTY.md") { git mv $f "docs\$f" }

# no longer used: workers.py is split into llm_agents\retry.py, transit.py, outreach.py
git rm -q workers.py
git rm -r -q --ignore-unmatch archive
if (Test-Path agents) { Remove-Item agents -Recurse -Force }   # leftover agents\auditor etc.
if (Test-Path __pycache__) { Remove-Item __pycache__ -Recurse -Force }

Write-Host "Done. Now copy the contents of v0.9.6_changed_files.zip into this folder (overwrite when asked), then run the tests."
