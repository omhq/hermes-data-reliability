#!/usr/bin/env bash
set -euo pipefail
umask 077

action=${1:-}
profile=${2:-}
configuration_source=${3:-}
from_terraform=${4:-}

if [[ "$action" != configure && "$action" != credentials ]]; then
    printf '%s\n' 'Choose configure or credentials.' >&2
    exit 1
fi
if [[ -z "$profile" || "$profile" == default ]]; then
    printf '%s\n' 'Select an explicit nondefault AWS profile.' >&2
    exit 1
fi
if ! command -v aws >/dev/null; then
    printf '%s\n' 'Install AWS CLI v2 before configuring.' >&2
    exit 1
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output_directory="$project_root/.local/docker"
compose=(docker compose --project-directory "$project_root" --file "$project_root/compose.yaml")
configuration_source=${configuration_source:-$project_root/.local/runtime.yaml}

if [[ "$action" == configure && "$from_terraform" != 1 && ! -f "$configuration_source" ]]; then
    printf '%s\n' 'Create .local/runtime.yaml from runtime.example.yaml, or set CONFIG_SOURCE or FROM_TERRAFORM=1.' >&2
    exit 1
fi
if [[ "$from_terraform" == 1 ]] && ! command -v terraform >/dev/null; then
    printf '%s\n' 'Terraform is required only when importing saved outputs.' >&2
    exit 1
fi

"${compose[@]}" build setup

setup_directory=$(mktemp -d "${TMPDIR:-/tmp}/hermes-setup.XXXXXX")
resource_next=""
cleanup() {
    rm -rf -- "$setup_directory"
    if [[ -n "$resource_next" ]]; then
        rm -f -- "$resource_next"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
    -u AWS_SECURITY_TOKEN aws configure export-credentials \
    --profile "$profile" --format process \
    > "$setup_directory/profile.json" 2> "$setup_directory/aws.stderr"; then
    printf '%s\n' 'Could not export the selected AWS profile. Refresh its login and retry. For SSO, use aws sso login --profile YOUR_PROFILE. Provider output was withheld.' >&2
    exit 1
fi

arguments=(
    "$action"
    --profile "$profile"
    --exported-credentials /run/hermes-setup/profile.json
    --destination /work
)
if [[ "$action" == configure ]]; then
    if [[ "$from_terraform" == 1 ]]; then
        if ! terraform -chdir="$project_root/infra/aws-agent" output -json investigator \
            > "$setup_directory/terraform.json" 2> "$setup_directory/terraform.stderr"; then
            printf '%s\n' 'Could not read saved agent Terraform outputs. Provider output was withheld.' >&2
            exit 1
        fi
        arguments+=(
            --terraform-output /run/hermes-setup/terraform.json
            --config /work/imported-runtime.yaml
        )
    else
        cp -- "$configuration_source" "$setup_directory/runtime.yaml"
        arguments+=(--config /run/hermes-setup/runtime.yaml)
    fi
fi

mkdir -p "$output_directory"
"${compose[@]}" run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    --volume "$setup_directory:/run/hermes-setup:ro" \
    --volume "$output_directory:/work" \
    setup "${arguments[@]}"

if [[ "$action" == configure && "$from_terraform" == 1 ]]; then
    mkdir -p "$(dirname "$configuration_source")"
    resource_next=$(mktemp "${configuration_source}.XXXXXX")
    cat "$output_directory/imported-runtime.yaml" > "$resource_next"
    mv -f -- "$resource_next" "$configuration_source"
    resource_next=""
    rm -f -- "$output_directory/imported-runtime.yaml"
fi
