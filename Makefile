PYTHON ?= python3
PROFILE_SCRIPT := scripts/fhir_pool_profiler.py
PILOT_SCRIPT := scripts/select_synthea_pilot50.py
PAIR_SCRIPT := scripts/generate_synthea_pilot50_pairs.py
REMAINING_SELECTION_SCRIPT := scripts/select_synthea_remaining70.py
REMAINING_PAIR_SCRIPT := scripts/generate_synthea_remaining70_pairs.py
REVIEWER_SCRIPT := scripts/package_ai_reviewer_requests.py
BATCH_SCRIPT := scripts/build_reviewer_batches.py

.PHONY: profile-large profile-dir generate-2000-and-profile select-pilot50 generate-pilot50-pairs select-remaining70 generate-remaining70-pairs package-ai-reviewers build-review-batches build-review-batches-remaining70

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

select-remaining70:
	$(PYTHON) $(REMAINING_SELECTION_SCRIPT) --output-dir outputs/large

generate-remaining70-pairs:
	$(PYTHON) $(REMAINING_PAIR_SCRIPT) --output-dir outputs/large

package-ai-reviewers:
	$(PYTHON) $(REVIEWER_SCRIPT) --output-dir outputs/large

build-review-batches:
	$(PYTHON) $(BATCH_SCRIPT) --output-dir outputs/large

build-review-batches-remaining70:
	$(PYTHON) $(BATCH_SCRIPT) \
		--pairs-file outputs/large/synthea_remaining70_pair_candidates.jsonl \
		--flags-file outputs/large/synthea_remaining70_pair_review_flags.csv \
		--output-dir outputs/large \
		--continue-numbering \
		--index-name reviewer_batch_index_remaining70.csv \
		--manifest-name reviewer_batch_manifest_remaining70.jsonl \
		--notes-name reviewer_batching_notes_remaining70.md \
		--high-risk-name high_risk_remaining70_pairs.csv
