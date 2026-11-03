SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
CONFIG_SOURCE ?=
FROM_TERRAFORM ?=
ARGS ?=

.PHONY: help configure credentials build up start stop down restart status logs login chat shell test check format dev

help:
	@printf '%s\n' \
	  'make configure AWS_PROFILE=YOUR_PROFILE  Configure an explicit profile and local resources' \
	  'make up                               Run with live logs; Ctrl+C stops the local containers' \
	  'make login                            Authenticate this local Hermes installation' \
	  'make chat                             Open Hermes chat (ARGS="--minutes 15")' \
	  'make status | logs | shell            Inspect the local runtime host' \
	  'make stop | start | restart            Control the shared runtime services' \
	  'make down                             Stop the host; keep all saved state' \
	  'make credentials AWS_PROFILE=YOUR_PROFILE  Refresh the selected AWS credentials' \
	  'make test | check                     Run tests or checks in disposable containers' \
	  'make format                           Format and fix lint in a disposable container' \
	  'make dev ARGS="python --version"        Run an ad hoc command in a disposable container'

configure:
	@test -n "$(AWS_PROFILE)" || { echo 'Set AWS_PROFILE explicitly.' >&2; exit 1; }
	@bash investigator/runtime/local-setup.sh configure "$(AWS_PROFILE)" "$(CONFIG_SOURCE)" "$(FROM_TERRAFORM)"

credentials:
	@test -n "$(AWS_PROFILE)" || { echo 'Set AWS_PROFILE explicitly.' >&2; exit 1; }
	@bash investigator/runtime/local-setup.sh credentials "$(AWS_PROFILE)"
	@if $(COMPOSE) ps --status running --services | grep -qx runtime; then \
	  $(COMPOSE) exec -T runtime /opt/hermes-lab-collector/bin/python -m investigator.runtime.local_host --credentials-only; \
	fi

build:
	$(COMPOSE) build runtime

up:
	@test -f .local/docker/investigator.json -a -f .local/docker/operations.json -a -f .local/docker/aws-credentials.json || { echo 'Run make configure AWS_PROFILE=YOUR_PROFILE first.' >&2; exit 1; }
	$(COMPOSE) up --build --force-recreate runtime

start:
	$(COMPOSE) exec runtime labctl start $(ARGS)

stop:
	$(COMPOSE) exec runtime labctl pause

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) exec runtime labctl restart $(ARGS)

status:
	$(COMPOSE) exec runtime labctl status

logs:
	$(COMPOSE) exec runtime journalctl -f -u 'hermes-lab-*' -u hermes-local-host.service $(ARGS)

login:
	$(COMPOSE) exec runtime labctl login

chat:
	$(COMPOSE) exec runtime labctl chat $(ARGS)

shell:
	$(COMPOSE) exec runtime bash

test:
	$(COMPOSE) build test
	$(COMPOSE) run --rm --no-deps test python -m unittest discover -s . -t . -v $(ARGS)

check:
	$(COMPOSE) build test
	$(COMPOSE) run --rm --no-deps test python -m ruff format --no-cache --check .
	$(COMPOSE) run --rm --no-deps test python -m ruff check --no-cache .
	$(COMPOSE) run --rm --no-deps -e PYTHONPYCACHEPREFIX=/tmp/hermes-pycache test python -m compileall -q aws_access observations collector incidents tool_boundary investigator pipeline evals tests

format:
	$(COMPOSE) build test
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" --volume "$(CURDIR):/workspace" --workdir /workspace test python -m ruff format --no-cache .
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" --volume "$(CURDIR):/workspace" --workdir /workspace test python -m ruff check --no-cache --fix .

dev:
	$(COMPOSE) build test
	$(COMPOSE) run --rm --no-deps test $(or $(ARGS),bash)
