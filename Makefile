.PHONY: help build down restart start build-images

PROJECT_DIR = $(CURDIR)
SERVICE_NAME = registry
COMPOSE_FILE = docker-compose.yml  # Path to your docker-compose file (if it's not the default)
INIT_SERVER_PATH = $(PROJECT_DIR)/infra/init_container/
INIT_FILE_PATH = $(INIT_SERVER_PATH)/apko_server.yaml
INIT_IMAGE_NAME = apko-server:latest

help: ## Show this help message
	@echo "W.O.R.F. Docker Management help"
	@echo "========================="
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build images
	@docker compose build > /dev/null 2>&1
	@echo "Building base images"

down: ## Stop all services
	@docker compose down > /dev/null 2>&1

restart: ## Restart all services
	@echo "Restarting WORF ---"
	@docker compose down > /dev/null 2>&1

start: ## Start W.O.R.F. 
	@clear
	@cat ./assets/logo.txt
	@docker run -v "$(INIT_SERVER_PATH)":/work cgr.dev/chainguard/apko \
		build apko_server.yaml apko-server:latest apko_init.tar \
		> /dev/null 2>&1
	@docker load < $(INIT_SERVER_PATH)/apko_init.tar > /dev/null 2>&1
	@echo "Starting W.O.R.F. network"
	@docker compose up --no-start > /dev/null 2>&1
	@echo "Starting: $(SERVICE_NAME)"
	@if ! docker compose -f $(COMPOSE_FILE) ps | grep -q $(SERVICE_NAME); then \
		echo "$(SERVICE_NAME) container is not running. Starting it..."; \
		docker compose -f $(COMPOSE_FILE) up -d $(SERVICE_NAME) > /dev/null 2>&1; \
	else \
		echo "  - $(SERVICE_NAME) container is already running."; \
	fi
	@echo "Building init container"
	@echo "Cleaning up working directory"
	@rm $(INIT_SERVER_PATH)/*.json -f
	@rm $(INIT_SERVER_PATH)/*.tar -f
	@docker compose -f docker-compose.yml up -d apko-flask-server > /dev/null 2>&1
	@echo "starting apk-server"
	@docker compose -f docker-compose.yml up -d apk-server > /dev/null 2>&1

build-images:
	@echo "---- Building Images Stored in /images now ----"
	@for file in infra/images/*.yaml; do \
		name=$$(basename $$file .yaml); \
		image_name=$$(echo $$name | cut -d_ -f1); \
		image_tag=$$(echo $$name | cut -d_ -f2); \
		echo "Building $$image_name:$$image_tag from $$file"; \
		curl -X POST \
			-F "image_name=$$image_name" \
			-F "image_tag=$$image_tag" \
			-F "file=@$$file" \
			http://localhost:8081/upload; \
	done
