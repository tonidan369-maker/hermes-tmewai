PYTHON ?= python3
BUILD ?= build
DEMO_EXPORT := reference/demo/facebook-export.zip
BIG_DIR := $(BUILD)/big-export
BIG_EXPORT := $(BIG_DIR)/facebook-export.zip

.PHONY: test demo big

test:
	$(PYTHON) -m unittest discover -s tests -v

demo:
	$(PYTHON) scripts/ingest_archive.py --fresh --label demo \
		--db $(BUILD)/demo.db $(DEMO_EXPORT)

big:
	$(PYTHON) reference/fixtures/make_export.py $(BIG_DIR)
	$(PYTHON) scripts/ingest_archive.py --fresh --label big \
		--db $(BUILD)/big.db $(BIG_EXPORT)
