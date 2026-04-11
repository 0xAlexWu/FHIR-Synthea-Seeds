PYTHON ?= python3
PROFILE_SCRIPT := scripts/fhir_pool_profiler.py
PILOT_SCRIPT := scripts/select_synthea_pilot50.py
PAIR_SCRIPT := scripts/generate_synthea_pilot50_pairs.py
REVIEWER_SCRIPT := scripts/package_ai_reviewer_requests.py

.PHONY: profile-large profile-dir generate-2000-and-profile select-pilot50 generate-pilot50-pairs package-ai-reviewers

profile-large:
	$(PYTHON) $(PROFILE_SCRIPT) profile-large --output-dir outputs/large

profile-dir:
	@if [ -z "$(DATASET_DIR)" ]; then echo "DATASET_DIR is required"; exit 2; fi
	@if [ -z "$(OUTPUT_DIR)" ]; then echo "OUTPUT_DIR is required"; exit 2; fi
	$(PYTHON) $(PROFILE_SCRIPT) profile-dir --dataset-dir "$(DATASET_DIR)" --output-dir "$(OUTPUT_DIR)"

generate-2000-and-profile:
	@if [ -z "$(REPO_DIR)" ]; then echo "REPO_DIR is required"; exit 2; fi
	$(PYTHON) $(PROFILE_SCRIPT) generate-and-profile --repo-dir "$(REPO_DIR)" --patient-count 2000 --output-dir outputs/custom-2000

select-pilot50:
	$(PYTHON) $(PILOT_SCRIPT) --output-dir outputs/large

generate-pilot50-pairs:
	$(PYTHON) $(PAIR_SCRIPT) --output-dir outputs/large

package-ai-reviewers:
	$(PYTHON) $(REVIEWER_SCRIPT) --output-dir outputs/large
