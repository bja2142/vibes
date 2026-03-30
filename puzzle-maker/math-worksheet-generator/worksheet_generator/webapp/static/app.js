async function fetchJson(path, options = {}) {
    const response = await fetch(path, {
        headers: { Accept: "application/json" },
        signal: options.signal,
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.message || `Request failed for ${path}: ${response.status}`);
    }
    return data;
}

async function sendJson(path, method, payload, options = {}) {
    const response = await fetch(path, {
        method,
        headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
        },
        body: JSON.stringify(payload),
        signal: options.signal,
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.message || `Request failed for ${path}: ${response.status}`);
    }
    return data;
}

const state = {
    config: null,
    currentDraft: null,
    currentGalleryItems: [],
    currentModalRun: null,
    maintenance: null,
    filters: {
        search: "",
        learner_band: "",
        reveal_mode: "",
        skill_profile: "",
        styling_status: "",
        picture_preset: "",
        difficulty_minimum: "",
        difficulty_maximum: "",
        sort: "created_desc",
        offset: 0,
        limit: 24,
    },
    workflow: {
        active: false,
        token: null,
        phase: "idle",
        controls: null,
        draft: null,
        run: null,
        job: null,
        abortController: null,
        startedAt: null,
        timerId: null,
        pollTimerId: null,
        lastJobMessage: "",
        stylingPending: false,
        preserveSolutionRewriteAction: false,
    },
};

const PANEL_STORAGE_KEY = "worksheet-panel-config-v2";
const IMAGE_ADDITIONAL_GUIDANCE_MAX_LENGTH = 500;
const WORKFLOW_STEPS = [
    { id: "draft", label: "Draft Generation" },
    { id: "review", label: "Review" },
    { id: "approval", label: "Approval" },
    { id: "worksheet", label: "Worksheet Generation" },
    { id: "plain_review", label: "Plain Review" },
    { id: "styling", label: "Styling" },
    { id: "verification", label: "Verification" },
    { id: "complete", label: "Complete" },
];

function jobPhaseLabel(phase, fallback = "In Progress") {
    return state.config?.job_tracking?.job_phase_catalog?.[phase] || fallback;
}

function runPhaseLabel(run, fallback = "Run Pending") {
    if (run?.lifecycle?.label) {
        return run.lifecycle.label;
    }
    const phase = run?.lifecycle?.phase;
    return state.config?.job_tracking?.run_phase_catalog?.[phase] || fallback;
}

function appendTextElement(parent, tagName, text, className = "") {
    const node = document.createElement(tagName);
    if (className) {
        node.className = className;
    }
    node.textContent = text;
    parent.appendChild(node);
    return node;
}

function isDebugUiEnabled() {
    return Boolean(state.config?.ui?.debug_enabled);
}

function formatRunTimestamp(value) {
    if (!value) {
        return "Just now";
    }
    const date = new Date(String(value).replace(" ", "T") + "Z");
    if (Number.isNaN(date.getTime())) {
        return "Recently";
    }
    return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function customerRunSummary(item) {
    const parts = [];
    if (item.theme) {
        parts.push(item.theme);
    }
    if (item.prompt_text) {
        parts.push(item.prompt_text);
    }
    return parts[0] || "Saved worksheet run";
}

function workflowTaskToken(run) {
    const token = String(run?.parameters?.workflow_token || "").trim();
    return token || null;
}

function shouldShowDiagnosticArtifacts(run) {
    if (isDebugUiEnabled()) {
        return true;
    }
    return ["styled_failed_plain_retained"].includes(run?.lifecycle?.phase || "");
}

function createImageCard({ imageUrl, label, altText = label }) {
    const wrapper = document.createElement("button");
    wrapper.type = "button";
    wrapper.className = "modal-image-card";
    wrapper.setAttribute("aria-label", `Preview ${label}`);

    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = altText;
    wrapper.appendChild(image);

    appendTextElement(wrapper, "span", label);
    wrapper.addEventListener("click", () => {
        openImagePreview(imageUrl, label);
    });
    return wrapper;
}

function createMiniPill(label) {
    const pill = document.createElement("span");
    pill.className = "mini-pill";
    pill.textContent = label;
    return pill;
}

function isTerminalJobStatus(status) {
    return ["completed", "failed", "cancelled"].includes(status);
}

function workflowStepStatuses() {
    const steps = Object.fromEntries(WORKFLOW_STEPS.map((step) => [step.id, "pending"]));
    const draft = state.workflow.draft;
    const run = state.workflow.run;
    const job = state.workflow.job;
    const phase = state.workflow.phase || "";
    const reviewVisible = !document.getElementById("workflow-review-section").hidden;
    const runReviewVisible = !document.getElementById("workflow-run-review-section").hidden;
    const stylingRequested = Boolean(state.workflow.controls?.apply_image_styling);

    if (draft || reviewVisible || run || phase.startsWith("draft_") || phase === "manual_review") {
        steps.draft = "complete";
    }
    if (phase.startsWith("draft_generation") || phase.startsWith("draft_regeneration")) {
        steps.draft = job?.status === "failed" ? "failed" : job?.status === "cancelled" ? "cancelled" : "current";
    }

    if (reviewVisible || draft) {
        steps.review = "current";
    }
    if ((draft && ["approved", "edited", "rejected"].includes(String(draft.approval_state).toLowerCase())) || run) {
        steps.review = "complete";
    }

    if (phase === "approving_draft") {
        steps.approval = "current";
    } else if (run || (draft && String(draft.approval_state).toLowerCase() === "approved")) {
        steps.approval = "complete";
    } else if (draft && String(draft.approval_state).toLowerCase() === "rejected") {
        steps.approval = "failed";
    }

    if (job?.job_type === "worksheet_generate" && !isTerminalJobStatus(job.status)) {
        steps.worksheet = "current";
    } else if (run?.lifecycle?.phase === "run_cancelled" || (job?.job_type === "worksheet_generate" && job?.status === "cancelled")) {
        steps.worksheet = "cancelled";
    } else if (job?.job_type === "worksheet_generate" && job?.status === "failed") {
        steps.worksheet = "failed";
    } else if (run) {
        steps.worksheet = "complete";
    }

    if (!stylingRequested && run) {
        steps.plain_review = "complete";
    } else if (run?.lifecycle?.can_confirm_styling || (runReviewVisible && !state.workflow.stylingPending && run?.lifecycle?.phase === "awaiting_styling_confirmation")) {
        steps.plain_review = "current";
    } else if (
        state.workflow.stylingPending ||
        (run?.lifecycle?.phase && ["styling_queued", "styling_running", "styled_verified", "styled_failed_plain_retained", "styling_cancelled_plain_retained"].includes(run.lifecycle.phase))
    ) {
        steps.plain_review = "complete";
    }

    if (!stylingRequested) {
        steps.styling = "skipped";
        steps.verification = "skipped";
    } else if (run?.lifecycle?.phase === "styling_cancelled_plain_retained" || job?.status === "cancelled") {
        steps.styling = "cancelled";
        steps.verification = "cancelled";
    } else if (run?.lifecycle?.phase === "styled_failed_plain_retained" || (job?.job_type === "worksheet_style" && job?.status === "failed")) {
        steps.styling = "failed";
        steps.verification = "failed";
    } else if (run?.lifecycle?.phase === "styled_verified") {
        steps.styling = "complete";
        steps.verification = "complete";
    } else if (job?.job_type === "worksheet_style" && !isTerminalJobStatus(job.status)) {
        if (phase === "styling_write_artifacts") {
            steps.styling = "complete";
            steps.verification = "current";
        } else {
            steps.styling = "current";
            steps.verification = "pending";
        }
    } else if (run?.lifecycle?.phase === "awaiting_styling_confirmation") {
        steps.styling = "pending";
        steps.verification = "pending";
    }

    if (run?.lifecycle?.phase === "run_cancelled") {
        steps.complete = "cancelled";
    } else if (run?.lifecycle?.phase === "run_failed" || job?.status === "failed") {
        steps.complete = "failed";
    } else if (run?.lifecycle?.is_terminal) {
        steps.complete = "complete";
    } else if (job && !isTerminalJobStatus(job.status)) {
        steps.complete = "pending";
    }

    return steps;
}

function workflowStepDescription(stepId, status) {
    const run = state.workflow.run;
    const draft = state.workflow.draft;
    const phase = state.workflow.phase || "";
    const currentJobMessage = state.workflow.job?.progress_message || "";
    if (status === "failed") {
        return currentJobMessage || "This step failed. Review the message above and try again.";
    }
    if (status === "cancelled") {
        return "This step was cancelled before the worksheet workflow completed.";
    }
    if (status === "skipped") {
        return "This step is not needed for the current worksheet settings.";
    }
    switch (stepId) {
        case "draft":
            if (status === "current") {
                return state.config.gemini.enabled
                    ? "Gemini is preparing the draft prompt and solution."
                    : "Gemini is unavailable, so the workflow will switch into manual review.";
            }
            return draft
                ? `Draft #${draft.id} is ready for review.`
                : "Generate or enter the draft that will drive this worksheet.";
        case "review":
            if (status === "current") {
                return state.workflow.controls?.reveal_mode === "color_by_number"
                    ? "Review the riddle and solution. The solution word guides the picture subject, while the question count comes from the palette."
                    : "Review the riddle and solution, then render the worksheet.";
            }
            return "The riddle has been reviewed.";
        case "approval":
            if (status === "current") {
                return "Saving your edits and approving the current riddle before worksheet rendering.";
            }
            return status === "complete" ? "The riddle is approved for worksheet rendering." : "Approval happens automatically when you render the worksheet.";
        case "worksheet":
            if (status === "current") {
                return currentJobMessage || "Rendering worksheet artifacts and saving the plain preview.";
            }
            return status === "complete" ? "The plain worksheet has been rendered." : "This step renders the worksheet preview and downloads.";
        case "plain_review":
            if (status === "current") {
                return "Inspect the plain worksheet preview, then either keep it as-is or continue into Gemini styling.";
            }
            return status === "complete" ? "The plain worksheet review step is complete." : "You only need this step when image styling is enabled.";
        case "styling":
            if (status === "current") {
                return currentJobMessage || "Gemini is styling the worksheet image while preserving the worksheet content.";
            }
            return status === "complete" ? "Gemini styling has finished." : "This step decorates the worksheet image after plain review.";
        case "verification":
            if (status === "current") {
                return currentJobMessage || "Verifying the Gemini result and applying the preserved worksheet overlay.";
            }
            return status === "complete" ? "The styled worksheet passed verification." : "Verification checks that text and content remain intact.";
        case "complete":
            if (status === "complete") {
                const token = workflowTaskToken(run);
                return run ? `${token ? `Task ${token}` : `Run #${run.id}`} is ready to open.` : "This worksheet run is complete.";
            }
            return "The finished worksheet will appear here when the workflow completes.";
        default:
            return "";
    }
}

function workflowActiveBackgroundJobType() {
    if (
        state.workflow.job &&
        !isTerminalJobStatus(state.workflow.job.status) &&
        ["worksheet_generate", "worksheet_style"].includes(state.workflow.job.job_type)
    ) {
        return state.workflow.job.job_type;
    }
    return null;
}

function canCancelActiveStylingJob() {
    const job = state.workflow.job;
    if (!job || job.job_type !== "worksheet_style" || isTerminalJobStatus(job.status)) {
        return false;
    }
    const phase = String(job.phase || "queued").trim() || "queued";
    return [
        "queued",
        "styling_queued",
        "styling_retry_queued",
        "styling_prepare",
        "styling_retry_prepare",
        "styling_render_foreground",
        "styling_refine_prompt",
    ].includes(phase);
}

function workflowIsBusyStep(stepId, status) {
    if (status !== "current") {
        return false;
    }
    const jobType = workflowActiveBackgroundJobType();
    if (stepId === "draft" || stepId === "approval") {
        return Boolean(state.workflow.abortController);
    }
    if (stepId === "worksheet") {
        return jobType === "worksheet_generate";
    }
    if (stepId === "styling") {
        return (state.workflow.stylingPending || jobType === "worksheet_style") && state.workflow.phase !== "styling_write_artifacts";
    }
    if (stepId === "verification") {
        return (state.workflow.stylingPending || jobType === "worksheet_style") && state.workflow.phase === "styling_write_artifacts";
    }
    return false;
}

function workflowStepActions(stepId, status) {
    const actions = [];
    if (status === "current") {
        if (stepId === "worksheet" && workflowActiveBackgroundJobType() === "worksheet_generate") {
            actions.push({
                id: "cancel",
                label: "Cancel Generation",
                variant: "secondary",
                disabled: false,
            });
        } else if (stepId === "styling" && workflowActiveBackgroundJobType() === "worksheet_style" && canCancelActiveStylingJob()) {
            actions.push({
                id: "cancel",
                label: "Cancel Styling",
                variant: "secondary",
                disabled: false,
            });
        }
    }
    return actions;
}

const WORKFLOW_INLINE_SECTION_IDS = [
    "workflow-locked-summary-section",
    "workflow-review-section",
    "workflow-run-review-section",
    "workflow-styled-response-section",
];

function resetWorkflowInlineSections() {
    const staging = document.getElementById("workflow-inline-staging");
    for (const sectionId of WORKFLOW_INLINE_SECTION_IDS) {
        const section = document.getElementById(sectionId);
        if (section && section.parentElement !== staging) {
            staging.appendChild(section);
        }
    }
}

function workflowInlineSectionsForStep(stepId, status) {
    if (status !== "current") {
        return [];
    }
    if (stepId === "review" && !document.getElementById("workflow-review-section").hidden) {
        return ["workflow-review-section"];
    }
    if (stepId === "worksheet" && !document.getElementById("workflow-locked-summary-section").hidden) {
        return ["workflow-locked-summary-section"];
    }
    if (stepId === "plain_review" && !document.getElementById("workflow-run-review-section").hidden) {
        const sections = [];
        if (!document.getElementById("workflow-locked-summary-section").hidden) {
            sections.push("workflow-locked-summary-section");
        }
        sections.push("workflow-run-review-section");
        return sections;
    }
    if ((stepId === "styling" || stepId === "verification") && !document.getElementById("workflow-styled-response-section").hidden) {
        return ["workflow-styled-response-section"];
    }
    return [];
}

function scrollWorkflowToCurrentStep() {
    const active = document.querySelector("#workflow-phase-list .workflow-phase-item.is-current");
    if (!active) {
        return;
    }
    active.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderWorkflowPhaseList() {
    const list = document.getElementById("workflow-phase-list");
    resetWorkflowInlineSections();
    list.innerHTML = "";
    const statuses = workflowStepStatuses();
    for (const step of WORKFLOW_STEPS) {
        const item = document.createElement("li");
        item.className = `workflow-phase-item is-${statuses[step.id] || "pending"}`;
        item.dataset.workflowStepId = step.id;
        const header = document.createElement("div");
        header.className = "workflow-phase-header";
        const titleGroup = document.createElement("div");
        titleGroup.className = "workflow-phase-title-group";
        const label = document.createElement("span");
        label.className = "workflow-phase-label";
        label.textContent = step.label;
        titleGroup.appendChild(label);
        if (workflowIsBusyStep(step.id, statuses[step.id])) {
            const spinner = document.createElement("span");
            spinner.className = "workflow-phase-spinner";
            spinner.setAttribute("aria-hidden", "true");
            titleGroup.appendChild(spinner);
        }
        const badge = document.createElement("span");
        badge.className = "workflow-phase-badge";
        badge.textContent =
            statuses[step.id] === "complete" ? "Done"
                : statuses[step.id] === "current" ? "Current"
                    : statuses[step.id] === "failed" ? "Failed"
                        : statuses[step.id] === "cancelled" ? "Cancelled"
                            : statuses[step.id] === "skipped" ? "Skipped"
                                : "Pending";
        header.appendChild(titleGroup);
        header.appendChild(badge);
        item.appendChild(header);
        const description = workflowStepDescription(step.id, statuses[step.id]);
        if (description) {
            appendTextElement(item, "p", description, "workflow-phase-detail");
        }
        const inlineSectionIds = workflowInlineSectionsForStep(step.id, statuses[step.id]);
        if (inlineSectionIds.length) {
            const content = document.createElement("div");
            content.className = "workflow-phase-content";
            for (const sectionId of inlineSectionIds) {
                const section = document.getElementById(sectionId);
                if (section) {
                    content.appendChild(section);
                }
            }
            item.appendChild(content);
        }
        const actions = workflowStepActions(step.id, statuses[step.id]);
        if (actions.length) {
            const actionRow = document.createElement("div");
            actionRow.className = "workflow-phase-actions";
            for (const action of actions) {
                const button = document.createElement("button");
                button.type = "button";
                button.dataset.workflowAction = action.id;
                button.textContent = action.label;
                button.disabled = Boolean(action.disabled);
                if (action.variant === "secondary") {
                    button.className = "secondary-button";
                }
                actionRow.appendChild(button);
            }
            item.appendChild(actionRow);
        }
        list.appendChild(item);
    }
    scrollWorkflowToCurrentStep();
}

function setRunDetailUrl(runId, { replace = false } = {}) {
    const url = new URL(window.location.href);
    url.searchParams.set("run", String(runId));
    url.searchParams.delete("workflow");
    const statePayload = { ...(window.history.state || {}), runId };
    if (replace) {
        window.history.replaceState(statePayload, "", url);
    } else {
        window.history.pushState(statePayload, "", url);
    }
}

function clearRunDetailUrl({ replace = false } = {}) {
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    const statePayload = { ...(window.history.state || {}) };
    delete statePayload.runId;
    if (replace) {
        window.history.replaceState(statePayload, "", url);
    } else {
        window.history.pushState(statePayload, "", url);
    }
}

function setWorkflowUrl(token, { replace = false } = {}) {
    const url = new URL(window.location.href);
    url.searchParams.set("workflow", String(token));
    url.searchParams.delete("run");
    const statePayload = { ...(window.history.state || {}), workflowToken: token };
    if (replace) {
        window.history.replaceState(statePayload, "", url);
    } else {
        window.history.pushState(statePayload, "", url);
    }
}

function clearWorkflowUrl({ replace = false } = {}) {
    const url = new URL(window.location.href);
    url.searchParams.delete("workflow");
    const statePayload = { ...(window.history.state || {}) };
    delete statePayload.workflowToken;
    if (replace) {
        window.history.replaceState(statePayload, "", url);
    } else {
        window.history.pushState(statePayload, "", url);
    }
}

function requestedWorkflowTokenFromUrl() {
    const url = new URL(window.location.href);
    const value = url.searchParams.get("workflow");
    return value && value.trim() ? value.trim() : null;
}

function requestedRunIdFromUrl() {
    const url = new URL(window.location.href);
    const value = url.searchParams.get("run");
    if (!value) {
        return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function populateSelect(element, options, { includeBlank = false, blankLabel = "All" } = {}) {
    element.innerHTML = "";
    if (includeBlank) {
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = blankLabel;
        element.appendChild(empty);
    }
    for (const option of options) {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.label;
        element.appendChild(node);
    }
}

function populatePictureSourceOptions() {
    const element = document.getElementById("color-picture-source");
    element.innerHTML = "";
    for (const option of state.config.worksheet_options.color_picture_sources) {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.label;
        node.disabled = option.enabled === false;
        element.appendChild(node);
    }
}

function setStatus(message, tone = "neutral") {
    const note = document.getElementById("form-note");
    note.textContent = message;
    note.dataset.tone = tone;
}

function selectedSkillsFromUi() {
    return Array.from(document.querySelectorAll("#skill-selection-list .skill-selection-item"))
        .map((node) => {
            const skill = node.dataset.skill || "";
            const enabled = node.querySelector(".skill-selection-checkbox")?.checked;
            const minimum = Number(node.querySelector(".skill-difficulty-minimum")?.value || 1);
            const maximum = Number(node.querySelector(".skill-difficulty-maximum")?.value || minimum);
            return enabled ? { skill, difficulty_minimum: minimum, difficulty_maximum: maximum } : null;
        })
        .filter(Boolean);
}

function derivedSkillProfileFromSelectedSkills(selectedSkills) {
    const skills = selectedSkills.map((item) => item.skill);
    if (!skills.length) {
        return "mixed_operations";
    }
    if (skills.length === 1) {
        return skills[0];
    }
    const skillSet = new Set(skills);
    if (skillSet.size === 2 && skillSet.has("addition") && skillSet.has("subtraction")) {
        return "subtraction_and_addition";
    }
    if (
        skillSet.size === 4 &&
        skillSet.has("addition") &&
        skillSet.has("subtraction") &&
        skillSet.has("multiplication") &&
        skillSet.has("division")
    ) {
        return "mixed_operations";
    }
    if (skillSet.size === 2 && skillSet.has("geometry") && skillSet.has("trigonometry")) {
        return "geometry";
    }
    return "mixed_skills";
}

function selectedSkillDifficultyBounds(selectedSkills) {
    if (!selectedSkills.length) {
        return { minimum: 1, maximum: 1 };
    }
    return {
        minimum: Math.min(...selectedSkills.map((item) => Number(item.difficulty_minimum))),
        maximum: Math.max(...selectedSkills.map((item) => Number(item.difficulty_maximum))),
    };
}

function legacySelectedSkillsFromProfile(skillProfile, difficultyMinimum = 1, difficultyMaximum = 2) {
    const profileMap = {
        subtraction_and_addition: ["addition", "subtraction"],
        multiplication_focus: ["multiplication"],
        division_focus: ["division"],
        algebra: ["algebra"],
        geometry: ["geometry", "trigonometry"],
        addition: ["addition"],
        subtraction: ["subtraction"],
        multiplication: ["multiplication"],
        division: ["division"],
        trigonometry: ["trigonometry"],
        mixed_operations: ["addition", "subtraction", "multiplication", "division"],
    };
    return (profileMap[skillProfile] || ["addition", "subtraction", "multiplication", "division"]).map((skill) => ({
        skill,
        difficulty_minimum: difficultyMinimum,
        difficulty_maximum: difficultyMaximum,
    }));
}

function setFormValidationMessage(message, tone = "error") {
    const node = document.getElementById("form-validation-message");
    if (!message) {
        node.hidden = true;
        node.textContent = "";
        delete node.dataset.tone;
        return;
    }
    node.hidden = false;
    node.textContent = message;
    node.dataset.tone = tone;
}

function validateControlPayloadPreflight(controls) {
    if (controls.difficulty_minimum > controls.difficulty_maximum) {
        return "Difficulty minimum must not exceed maximum.";
    }
    if (!Array.isArray(controls.selected_skills) || controls.selected_skills.length === 0) {
        return "Select at least one challenge type.";
    }
    for (const selection of controls.selected_skills) {
        if (Number(selection.difficulty_minimum) > Number(selection.difficulty_maximum)) {
            return `Difficulty minimum must not exceed maximum for ${selection.skill}.`;
        }
    }
    return "";
}

function collectControlPayload() {
    const selected_skills = selectedSkillsFromUi();
    const difficultyBounds = selectedSkillDifficultyBounds(selected_skills);
    return {
        theme: document.getElementById("theme").value.trim(),
        learner_band: document.getElementById("learner-band").value,
        style: document.getElementById("reward-style").value,
        language: document.getElementById("reward-language").value,
        reveal_mode: document.getElementById("reveal-mode").value,
        skill_profile: derivedSkillProfileFromSelectedSkills(selected_skills),
        selected_skills,
        difficulty_minimum: difficultyBounds.minimum,
        difficulty_maximum: difficultyBounds.maximum,
        decoy_percentage: Number(document.getElementById("decoy-percentage").value),
        color_picture_source: document.getElementById("color-picture-source").value,
        color_picture_preset: document.getElementById("color-picture-preset").value,
        apply_image_styling: document.getElementById("apply-image-styling").checked,
        image_style_name: document.getElementById("image-style-name").value,
        image_color_mode: document.getElementById("image-color-mode").value,
        image_ink_saver: document.getElementById("image-ink-saver").checked,
        image_additional_guidance: document.getElementById("image-additional-guidance").value.trim(),
        seed: Number(document.getElementById("seed").value) || null,
    };
}

function workflowControlsFromRun(run) {
    const parameters = run.parameters || {};
    const selectedSkills = Array.isArray(parameters.selected_skills) ? parameters.selected_skills : [];
    return {
        theme: run.theme || parameters.theme || "",
        learner_band: run.learner_band,
        style: "question",
        language: "en",
        reveal_mode: run.reveal_mode,
        skill_profile: run.skill_profile,
        selected_skills: selectedSkills,
        difficulty_minimum: Number(parameters.difficulty_minimum || selectedSkillDifficultyBounds(selectedSkills).minimum || 1),
        difficulty_maximum: Number(parameters.difficulty_maximum || selectedSkillDifficultyBounds(selectedSkills).maximum || 1),
        decoy_percentage: Number(parameters.decoy_percentage || 0),
        color_picture_source: parameters.color_picture_source || "preset",
        color_picture_preset: parameters.color_picture_preset || "smile",
        apply_image_styling: Boolean(run.styling?.requested),
        image_style_name: run.styling?.style_name || state.config.worksheet_options.image_styling.default_style,
        image_color_mode: run.styling?.color_mode || state.config.worksheet_options.image_styling.default_color_mode,
        image_ink_saver: Boolean(run.styling?.ink_saver),
        image_additional_guidance: parameters.image_additional_guidance || "",
        seed: parameters.seed || null,
    };
}

function safeLocalStorage() {
    try {
        return window.localStorage;
    } catch (error) {
        return null;
    }
}

function persistPanelState() {
    const storage = safeLocalStorage();
    if (!storage) {
        return;
    }
    storage.setItem(PANEL_STORAGE_KEY, JSON.stringify(collectControlPayload()));
}

function readPanelState() {
    const storage = safeLocalStorage();
    if (!storage) {
        return null;
    }
    const raw = storage.getItem(PANEL_STORAGE_KEY);
    if (!raw) {
        return null;
    }
    try {
        return JSON.parse(raw);
    } catch (error) {
        storage.removeItem(PANEL_STORAGE_KEY);
        return null;
    }
}

function optionExists(options, value) {
    return options.some((option) => option.value === value);
}

function difficultyToGridSize(difficulty) {
    return 16 + (Math.max(1, Math.min(5, Number(difficulty))) - 1) * 12;
}

function difficultyToColorCount(difficulty) {
    return {
        1: 4,
        2: 6,
        3: 8,
        4: 16,
        5: 32,
    }[Math.max(1, Math.min(5, Number(difficulty)))];
}

function updateDecoyFieldState() {
    const revealMode = document.getElementById("reveal-mode").value;
    const group = document.getElementById("letter-bank-options");
    const decoyField = document.getElementById("decoy-percentage");
    const applies = revealMode === "letter_bank";
    group.hidden = !applies;
    decoyField.disabled = !applies;
    decoyField.title = applies ? "" : "Lookup decoys only apply to letter-bank worksheets.";
}

function updateColorPictureControls() {
    const revealMode = document.getElementById("reveal-mode").value;
    const group = document.getElementById("color-by-number-options");
    const source = document.getElementById("color-picture-source");
    const preset = document.getElementById("color-picture-preset");
    const note = document.getElementById("color-picture-note");
    const colorDifficultyConfig = state.config.worksheet_options.color_by_number_difficulty_range;
    const isColorMode = revealMode === "color_by_number";
    group.hidden = !isColorMode;
    source.disabled = !isColorMode;
    const usesPreset = source.value !== "gemini";
    preset.disabled = !isColorMode || !usesPreset;
    updateSkillSelectionState();
    const controls = collectControlPayload();
    const gridSize = difficultyToGridSize(controls.difficulty_maximum);
    if (!isColorMode) {
        return;
    }
    if (source.value === "gemini") {
        note.textContent = state.config.gemini.enabled
            ? `Gemini will generate a square ${gridSize}x${gridSize} picture grid and use ${difficultyToColorCount(controls.difficulty_maximum)} palette colors. This is an experimental path and may fall back to a safe render if Gemini cannot return a valid grid. ${colorDifficultyConfig.note}`
            : "Gemini picture generation is disabled because GEMINI_API_KEY was not detected.";
    } else {
        note.textContent = `Preset color-by-number pictures use a square ${gridSize}x${gridSize} grid with ${difficultyToColorCount(controls.difficulty_maximum)} palette colors. ${colorDifficultyConfig.note}`;
    }
}

function updateImageStylingControls() {
    const stylingConfig = state.config.worksheet_options.image_styling;
    const toggle = document.getElementById("apply-image-styling");
    const styleField = document.getElementById("image-style-name");
    const colorModeField = document.getElementById("image-color-mode");
    const inkSaverField = document.getElementById("image-ink-saver");
    const additionalGuidanceField = document.getElementById("image-additional-guidance");
    const note = document.getElementById("image-styling-note");
    if (!stylingConfig.enabled) {
        toggle.checked = false;
    }
    const active = stylingConfig.enabled && toggle.checked;
    styleField.disabled = !active;
    colorModeField.disabled = !active;
    inkSaverField.disabled = !active;
    additionalGuidanceField.disabled = !active;
    note.textContent = stylingConfig.enabled
        ? (active
            ? `Image styling is requested and will be stored with this worksheet run. Target model: ${stylingConfig.model}.`
            : stylingConfig.note)
        : stylingConfig.note;
}

function countLetters(text) {
    return Array.from(text || "").filter((character) => /[A-Za-z]/.test(character)).length;
}

function currentLearnerBandPreset() {
    const learnerBand = document.getElementById("learner-band").value;
    return state.config.worksheet_options.learner_bands.find((band) => band.value === learnerBand) || null;
}

function skillSelectionConfigMap() {
    return new Map(state.config.worksheet_options.skills.map((skill) => [skill.value, skill]));
}

function setSelectedSkillsUi(selectedSkills, { force = false } = {}) {
    const selectedMap = new Map((selectedSkills || []).map((item) => [item.skill, item]));
    for (const node of document.querySelectorAll("#skill-selection-list .skill-selection-item")) {
        const skill = node.dataset.skill || "";
        const checkbox = node.querySelector(".skill-selection-checkbox");
        const minimum = node.querySelector(".skill-difficulty-minimum");
        const maximum = node.querySelector(".skill-difficulty-maximum");
        const selection = selectedMap.get(skill);
        if (selection || force) {
            checkbox.checked = Boolean(selection);
        }
        if (selection) {
            minimum.value = String(selection.difficulty_minimum);
            maximum.value = String(selection.difficulty_maximum);
        }
    }
    updateSkillSelectionState();
}

function updateSkillSelectionState() {
    const learnerBand = document.getElementById("learner-band").value;
    const revealMode = document.getElementById("reveal-mode").value;
    const maxDifficulty = revealMode === "color_by_number"
        ? state.config.worksheet_options.color_by_number_difficulty_range.maximum
        : state.config.worksheet_options.difficulty_range.maximum;
    const minDifficulty = state.config.worksheet_options.difficulty_range.minimum;
    for (const node of document.querySelectorAll("#skill-selection-list .skill-selection-item")) {
        const skillConfig = skillSelectionConfigMap().get(node.dataset.skill || "");
        const supported = (skillConfig?.supported_learner_bands || []).includes(learnerBand);
        const checkbox = node.querySelector(".skill-selection-checkbox");
        const minimum = node.querySelector(".skill-difficulty-minimum");
        const maximum = node.querySelector(".skill-difficulty-maximum");
        node.classList.toggle("is-disabled", !supported);
        checkbox.disabled = !supported;
        if (!supported) {
            checkbox.checked = false;
        }
        minimum.disabled = !supported || !checkbox.checked;
        maximum.disabled = !supported || !checkbox.checked;
        minimum.max = String(maxDifficulty);
        maximum.max = String(maxDifficulty);
        minimum.min = String(minDifficulty);
        maximum.min = String(minDifficulty);
        minimum.value = String(Math.min(Number(minimum.value || minDifficulty), maxDifficulty));
        maximum.value = String(Math.min(Number(maximum.value || minDifficulty), maxDifficulty));
        if (Number(minimum.value) > Number(maximum.value)) {
            minimum.value = maximum.value;
        }
    }
}

function renderSkillSelectionControls() {
    const container = document.getElementById("skill-selection-list");
    container.innerHTML = "";
    for (const skill of state.config.worksheet_options.skills) {
        const item = document.createElement("div");
        item.className = "skill-selection-item";
        item.dataset.skill = skill.value;

        const header = document.createElement("div");
        header.className = "skill-selection-header";
        const toggle = document.createElement("label");
        toggle.className = "skill-selection-toggle";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "skill-selection-checkbox";
        const label = document.createElement("span");
        label.textContent = skill.label;
        toggle.appendChild(checkbox);
        toggle.appendChild(label);
        header.appendChild(toggle);
        item.appendChild(header);

        const difficultyGrid = document.createElement("div");
        difficultyGrid.className = "skill-difficulty-grid";
        const minLabel = document.createElement("label");
        minLabel.className = "field";
        appendTextElement(minLabel, "span", "Minimum Difficulty");
        const minInput = document.createElement("input");
        minInput.type = "number";
        minInput.min = "1";
        minInput.max = "5";
        minInput.value = "1";
        minInput.className = "skill-difficulty-minimum";
        minLabel.appendChild(minInput);
        const maxLabel = document.createElement("label");
        maxLabel.className = "field";
        appendTextElement(maxLabel, "span", "Maximum Difficulty");
        const maxInput = document.createElement("input");
        maxInput.type = "number";
        maxInput.min = "1";
        maxInput.max = "5";
        maxInput.value = "2";
        maxInput.className = "skill-difficulty-maximum";
        maxLabel.appendChild(maxInput);
        difficultyGrid.appendChild(minLabel);
        difficultyGrid.appendChild(maxLabel);
        item.appendChild(difficultyGrid);
        container.appendChild(item);
    }
    updateSkillSelectionState();
}

function applyPanelState(saved) {
    if (!saved) {
        return false;
    }
    const learnerBandOptions = state.config.worksheet_options.learner_bands;
    const revealModeOptions = state.config.worksheet_options.reveal_modes;
    const rewardStyleOptions = state.config.worksheet_options.reward_styles;
    const languageOptions = state.config.worksheet_options.languages;
    const pictureSourceOptions = state.config.worksheet_options.color_picture_sources.filter((option) => option.enabled !== false);
    const picturePresetOptions = state.config.worksheet_options.color_picture_presets;
    const imageStyleOptions = state.config.worksheet_options.image_styling.styles;
    const imageColorModes = state.config.worksheet_options.image_styling.color_modes;

    if (!optionExists(learnerBandOptions, saved.learner_band)) {
        return false;
    }

    document.getElementById("theme").value = typeof saved.theme === "string" ? saved.theme : "";
    document.getElementById("learner-band").value = saved.learner_band;
    applyLearnerBandPreset(false);

    if (optionExists(revealModeOptions, saved.reveal_mode)) {
        document.getElementById("reveal-mode").value = saved.reveal_mode;
    }
    if (optionExists(rewardStyleOptions, saved.style)) {
        document.getElementById("reward-style").value = saved.style;
    }
    if (optionExists(languageOptions, saved.language)) {
        document.getElementById("reward-language").value = saved.language;
    }

    document.getElementById("decoy-percentage").value = String(saved.decoy_percentage ?? document.getElementById("decoy-percentage").value);
    document.getElementById("seed").value = saved.seed ?? "";

    if (optionExists(pictureSourceOptions, saved.color_picture_source)) {
        document.getElementById("color-picture-source").value = saved.color_picture_source;
    }
    if (optionExists(picturePresetOptions, saved.color_picture_preset)) {
        document.getElementById("color-picture-preset").value = saved.color_picture_preset;
    }
    document.getElementById("apply-image-styling").checked = Boolean(saved.apply_image_styling) && state.config.worksheet_options.image_styling.enabled;
    if (optionExists(imageStyleOptions, saved.image_style_name)) {
        document.getElementById("image-style-name").value = saved.image_style_name;
    }
    if (optionExists(imageColorModes, saved.image_color_mode)) {
        document.getElementById("image-color-mode").value = saved.image_color_mode;
    }
    document.getElementById("image-ink-saver").checked = Boolean(saved.image_ink_saver);
    document.getElementById("image-additional-guidance").value = typeof saved.image_additional_guidance === "string"
        ? saved.image_additional_guidance
        : "";
    updateStyleGuidanceStatus({
        countId: "image-additional-guidance-count",
        guidance: document.getElementById("image-additional-guidance").value,
    });

    setSelectedSkillsUi(
        Array.isArray(saved.selected_skills) && saved.selected_skills.length
            ? saved.selected_skills
            : legacySelectedSkillsFromProfile(
                saved.skill_profile || currentLearnerBandPreset()?.default_skill_profile || "mixed_operations",
                saved.difficulty_minimum ?? 1,
                saved.difficulty_maximum ?? 2,
            ),
        { force: true },
    );
    updateDecoyFieldState();
    updateColorPictureControls();
    updateImageStylingControls();
    persistPanelState();
    return true;
}

function applyLearnerBandPreset(force = false) {
    const preset = currentLearnerBandPreset();
    if (!preset) {
        return;
    }
    document.getElementById("learner-band-preset-note").textContent =
        `${preset.description} You can override the fields below after the preset is applied.`;
    if (force) {
        document.getElementById("reveal-mode").value = preset.default_reveal_mode;
        document.getElementById("decoy-percentage").value = preset.default_decoy_percentage;
        document.getElementById("color-picture-source").value = preset.default_color_picture_source;
        document.getElementById("color-picture-preset").value = preset.default_color_picture_preset;
        setSelectedSkillsUi(preset.default_selected_skills || [], { force: true });
    }
    updateSkillSelectionState();
    updateColorPictureControls();
    setFormValidationMessage(validateControlPayloadPreflight(collectControlPayload()));
}

function updateGeminiState() {
    const enabled = Boolean(state.config.gemini.enabled);
    const pill = document.getElementById("gemini-status-pill");
    const note = document.getElementById("gemini-status-note");

    note.textContent = state.config.gemini.note;
    pill.textContent = enabled ? `Enabled: ${state.config.gemini.model}` : "Disabled";
    pill.classList.toggle("warning", !enabled);
}

function updateRuntimeMetadata() {
    const list = document.getElementById("runtime-metadata");
    list.innerHTML = "";
    const items = [
        `DB: ${state.config.storage.database_path}`,
        `Artifacts: ${state.config.storage.artifact_root}`,
        `Gemini model: ${state.config.gemini.model}`,
        `Logging: ${state.config.logging.verbosity}`,
        `Job transport: ${state.config.job_tracking.transport}`,
        state.config.job_tracking.note,
    ];

    if (state.currentDraft) {
        items.push(`Current draft: #${state.currentDraft.id} (${state.currentDraft.approval_state})`);
    }

    for (const item of items) {
        const node = document.createElement("li");
        node.textContent = item;
        list.appendChild(node);
    }
}

function renderMaintenanceMetadata(snapshot) {
    state.maintenance = snapshot;
    const list = document.getElementById("maintenance-metadata");
    list.innerHTML = "";
    const items = [
        `Worksheet runs: ${snapshot.counts.worksheet_runs}`,
        `Artifacts: ${snapshot.counts.artifacts}`,
        `Jobs: ${snapshot.counts.jobs}`,
        `Run directories: ${snapshot.run_directory_count}`,
        `Orphan run directories: ${snapshot.orphan_run_directory_count}`,
    ];
    if (snapshot.orphan_run_directories?.length) {
        items.push(`Orphans: ${snapshot.orphan_run_directories.join(", ")}`);
    }
    for (const item of items) {
        const node = document.createElement("li");
        node.textContent = item;
        list.appendChild(node);
    }
}

function applyDebugUiMode() {
    const enabled = isDebugUiEnabled();
    document.getElementById("runtime-panel").hidden = !enabled;
    document.getElementById("maintenance-panel").hidden = !enabled;
    document.getElementById("workflow-log-section").hidden = !enabled;
    document.getElementById("workflow-config-section").hidden = !enabled;
    document.getElementById("modal-meta-section").hidden = !enabled;
}

function renderDraft(draft) {
    state.currentDraft = draft;
    updateGeminiState();
    updateRuntimeMetadata();
}

function setFormDisabled(disabled) {
    const form = document.getElementById("worksheet-config-form");
    for (const element of form.querySelectorAll("input, select, textarea, button")) {
        element.disabled = disabled;
    }
}

function formatElapsed(milliseconds) {
    const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
    const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
}

function currentElapsedText() {
    if (!state.workflow.startedAt) {
        return "00:00";
    }
    return formatElapsed(Date.now() - state.workflow.startedAt);
}

function updateWorkflowTimer() {
    document.getElementById("workflow-timer").textContent = currentElapsedText();
}

function startWorkflowTimer() {
    stopWorkflowTimer();
    state.workflow.startedAt = Date.now();
    updateWorkflowTimer();
    state.workflow.timerId = window.setInterval(updateWorkflowTimer, 1000);
}

function stopWorkflowTimer() {
    if (state.workflow.timerId) {
        window.clearInterval(state.workflow.timerId);
        state.workflow.timerId = null;
    }
}

function appendWorkflowLog(message, tone = "neutral") {
    const list = document.getElementById("workflow-log");
    const item = document.createElement("li");
    appendTextElement(item, "strong", currentElapsedText());
    appendTextElement(item, "span", message);
    item.dataset.tone = tone;
    list.appendChild(item);
}

function setWorkflowStatus(message, tone = "neutral", phaseLabel = null) {
    const node = document.getElementById("workflow-status-message");
    const pill = document.getElementById("workflow-state-pill");
    node.textContent = message;
    node.dataset.tone = tone;
    if (phaseLabel) {
        pill.textContent = phaseLabel;
    }
    pill.classList.toggle("warning", tone === "working");
    renderWorkflowPhaseList();
}

function renderWorkflowParameters(controls) {
    const list = document.getElementById("workflow-parameters");
    list.innerHTML = "";
    const items = [
        `Theme: ${controls.theme || "n/a"}`,
        `Learner band: ${controls.learner_band.replaceAll("_", " ")}`,
        `Reveal mode: ${controls.reveal_mode.replaceAll("_", " ")}`,
        `Challenge types: ${(controls.selected_skills || []).map((item) => `${item.skill} (${item.difficulty_minimum}-${item.difficulty_maximum})`).join(", ") || controls.skill_profile.replaceAll("_", " ")}`,
        `Difficulty span: ${controls.difficulty_minimum} to ${controls.difficulty_maximum}`,
        `Color picture: ${controls.color_picture_source === "gemini" ? "Gemini" : controls.color_picture_preset}`,
        `Image styling: ${controls.apply_image_styling ? "requested" : "not requested"}`,
        `Reward style: ${controls.style}`,
        `Language: ${controls.language}`,
        `Seed: ${controls.seed ?? "auto"}`,
    ];
    if (controls.apply_image_styling) {
        items.splice(6, 0, `Image style: ${controls.image_style_name}`);
        items.splice(7, 0, `Image color mode: ${controls.image_color_mode.replaceAll("_", " ")}`);
        items.splice(8, 0, `Ink saver: ${controls.image_ink_saver ? "yes" : "no"}`);
        if (controls.image_additional_guidance) {
            items.splice(9, 0, `Image guidance: ${controls.image_additional_guidance}`);
        }
    }
    if (controls.reveal_mode === "letter_bank") {
        items.splice(5, 0, `Lookup decoys: ${controls.decoy_percentage}%`);
    } else {
        items.splice(5, 0, `Color palette size: ${difficultyToColorCount(controls.difficulty_maximum)} questions`);
    }
    for (const entry of items) {
        const node = document.createElement("li");
        node.textContent = entry;
        list.appendChild(node);
    }
}

function hideWorkflowLockedSummary() {
    document.getElementById("workflow-locked-summary-section").hidden = true;
    document.getElementById("workflow-locked-summary-title").textContent = "Locked Worksheet Content";
    document.getElementById("workflow-locked-summary-meta").textContent = "Locked";
    document.getElementById("workflow-locked-summary-note").textContent =
        "This content is now fixed for the active worksheet workflow.";
    document.getElementById("workflow-locked-prompt").textContent = "";
    document.getElementById("workflow-locked-solution").textContent = "";
}

function renderWorkflowLockedSummary({ title, meta, note, promptText, solutionText }) {
    document.getElementById("workflow-locked-summary-section").hidden = false;
    document.getElementById("workflow-locked-summary-title").textContent = title;
    document.getElementById("workflow-locked-summary-meta").textContent = meta;
    document.getElementById("workflow-locked-summary-note").textContent = note;
    document.getElementById("workflow-locked-prompt").textContent = promptText || "No prompt stored.";
    document.getElementById("workflow-locked-solution").textContent = solutionText || "No solution stored.";
}

function updateWorkflowDerivedProblemCount() {
    const solutionText = document.getElementById("workflow-review-solution").value || "";
    const solutionCount = countLetters(solutionText);
    const primaryLabel = document.getElementById("workflow-derived-problem-count-label");
    const secondaryLabel = document.getElementById("workflow-derived-total-question-count-label");
    if (state.workflow.controls?.reveal_mode === "color_by_number") {
        const paletteCount = difficultyToColorCount(state.workflow.controls?.difficulty_maximum || 1);
        primaryLabel.textContent = "Solution Letter Count";
        secondaryLabel.textContent = "Palette Question Count";
        document.getElementById("workflow-derived-problem-count").value = String(solutionCount);
        document.getElementById("workflow-derived-total-question-count").value = String(paletteCount);
        return;
    }
    const decoyPercentage = Number(state.workflow.controls?.decoy_percentage || 0);
    const decoyCount = Math.max(0, Math.round((solutionCount * decoyPercentage) / 100));
    primaryLabel.textContent = "Solution Letter Count";
    secondaryLabel.textContent = "Lookup Decoy Count";
    document.getElementById("workflow-derived-problem-count").value = String(solutionCount);
    document.getElementById("workflow-derived-total-question-count").value = String(decoyCount);
}

function normalizeWorkflowSolutionValue(value) {
    return String(value || "").trim().replace(/\s+/g, " ");
}

function canRegenerateWorkflowPromptFromSolution() {
    if (!state.config?.gemini?.enabled || !state.workflow.draft) {
        return false;
    }
    const currentSolution = normalizeWorkflowSolutionValue(document.getElementById("workflow-review-solution").value);
    const savedSolution = normalizeWorkflowSolutionValue(state.workflow.draft.solution_phrase);
    return currentSolution.length > 0 && (state.workflow.preserveSolutionRewriteAction || currentSolution !== savedSolution);
}

function renderWorkflowDraft(draft, { preserveSolutionRewriteAction = false } = {}) {
    state.workflow.draft = draft;
    state.workflow.preserveSolutionRewriteAction = preserveSolutionRewriteAction;
    state.workflow.phase = "draft_review";
    renderDraft(draft);
    hideWorkflowLockedSummary();
    document.getElementById("workflow-run-review-section").hidden = true;
    document.getElementById("workflow-styled-response-section").hidden = true;
    document.getElementById("workflow-review-section").hidden = false;
    document.getElementById("workflow-review-prompt").value = draft.prompt_text || "";
    document.getElementById("workflow-review-solution").value = draft.solution_phrase || "";
    document.getElementById("workflow-draft-meta").textContent =
        `Draft #${draft.id} · ${draft.approval_state} · ${draft.source}`;
    document.getElementById("workflow-review-note").textContent =
        String(draft.approval_state).toLowerCase() === "rejected"
            ? "This draft is rejected. You can edit it to continue or regenerate a new riddle."
            : state.workflow.controls?.reveal_mode === "color_by_number"
            ? "Edit the riddle if needed. If you change the solution text, you can ask Gemini to write a new clue for that answer. For color-by-number, the solution word drives the picture subject while the question count comes from the palette size."
            : "Edit the riddle if needed. If you change the solution text, you can ask Gemini to write a new clue for that answer. Render Worksheet will save your edits, approve the riddle, and render worksheet artifacts.";
    updateWorkflowDerivedProblemCount();
    updateWorkflowActionState();
    renderWorkflowPhaseList();
}

function renderManualReview() {
    state.workflow.draft = null;
    state.currentDraft = null;
    state.workflow.phase = "manual_review";
    hideWorkflowLockedSummary();
    document.getElementById("workflow-review-section").hidden = false;
    document.getElementById("workflow-review-prompt").value = "";
    document.getElementById("workflow-review-solution").value = "";
    document.getElementById("workflow-draft-meta").textContent = "Manual review · unsaved";
    document.getElementById("workflow-review-note").textContent =
        "Gemini is unavailable. Enter the riddle and solution here, then render the worksheet.";
    updateWorkflowDerivedProblemCount();
    updateWorkflowActionState();
    updateRuntimeMetadata();
    renderWorkflowPhaseList();
}

function workflowReviewArtifacts(run) {
    const priority = {
        worksheet_preview: 0,
        worksheet_solution: 1,
    };
    return (run.artifacts || []).filter((artifact) =>
        artifact.output_format === "png" && ["worksheet_preview", "worksheet_solution"].includes(artifact.artifact_kind)
    ).sort((left, right) => compareArtifacts(left, right, priority));
}

function currentWorkflowStylingGuidance() {
    return document.getElementById("workflow-image-additional-guidance").value.trim();
}

function currentModalStylingGuidance() {
    return document.getElementById("modal-image-additional-guidance").value.trim();
}

function updateStyleGuidanceStatus({ countId, guidance, previewId = null }) {
    const raw = String(guidance || "");
    const normalized = raw.trim();
    const count = document.getElementById(countId);
    count.textContent = `${raw.length} / ${IMAGE_ADDITIONAL_GUIDANCE_MAX_LENGTH} characters`;
    if (!previewId) {
        return;
    }
    const preview = document.getElementById(previewId);
    preview.textContent = normalized
        ? `Guidance to Gemini: ${normalized}`
        : "No additional guidance will be sent to Gemini.";
}

function renderWorkflowRunReview(run) {
    state.workflow.run = run;
    state.workflow.phase = run.lifecycle?.phase || state.workflow.phase;
    const taskToken = workflowTaskToken(run);
    document.getElementById("workflow-review-section").hidden = true;
    document.getElementById("workflow-run-review-section").hidden = false;
    renderWorkflowLockedSummary({
        title: "Approved Worksheet Content",
        meta: "Locked",
        note: run.lifecycle?.can_confirm_styling
            ? "The prompt and solution are locked. Review the plain worksheet below, then decide whether to continue into Gemini styling."
            : "The prompt and solution are locked for this worksheet run.",
        promptText: run.prompt_text,
        solutionText: run.solution_phrase,
    });
    document.getElementById("workflow-run-meta").textContent =
        `${taskToken ? `Task ${taskToken}` : `Run #${run.id}`} · ${runPhaseLabel(run)}`;
    document.getElementById("workflow-run-note").textContent =
        run.lifecycle?.phase === "awaiting_styling_confirmation"
            ? "Review the plain worksheet and solution guide below. Then either continue into Gemini image styling or keep the plain worksheet only."
            : "This workflow is tracking the styling request. The plain worksheet and solution guide stay above, and any Gemini response appears below.";
    document.getElementById("workflow-image-guidance-field").hidden = !run.lifecycle?.can_confirm_styling;
    document.getElementById("workflow-image-additional-guidance").value =
        state.workflow.controls?.image_additional_guidance
        || run.parameters?.image_additional_guidance
        || "";
    updateStyleGuidanceStatus({
        previewId: "workflow-image-guidance-preview",
        countId: "workflow-image-guidance-count",
        guidance: document.getElementById("workflow-image-additional-guidance").value,
    });

    const imageGrid = document.getElementById("workflow-run-images");
    imageGrid.innerHTML = "";
    for (const artifact of workflowReviewArtifacts(run)) {
        imageGrid.appendChild(
            createImageCard({
                imageUrl: artifactUrl(artifact.relative_path),
                label: artifact.display_name,
            })
        );
    }
    const styledSection = document.getElementById("workflow-styled-response-section");
    const styledMeta = document.getElementById("workflow-styled-response-meta");
    const styledNote = document.getElementById("workflow-styled-response-note");
    const styledGrid = document.getElementById("workflow-styled-images");
    const styledArtifacts = (run.artifacts || []).filter((artifact) =>
        artifact.output_format === "png" && (
            artifact.artifact_kind === "worksheet_styled_background" ||
            artifact.artifact_kind === "worksheet_styled_preview" ||
            artifact.artifact_kind === "worksheet_styling_verification"
        )
    ).sort((left, right) => {
        const priority = {
            worksheet_styled_background: 0,
            worksheet_styled_preview: 1,
            worksheet_styling_verification: 2,
        };
        return compareArtifacts(left, right, priority);
    });
    styledGrid.innerHTML = "";
    if (styledArtifacts.length) {
        styledSection.hidden = false;
        styledMeta.textContent = runPhaseLabel(run, "Response Ready");
        styledNote.textContent =
            run.lifecycle?.phase === "styled_verified"
                ? "The raw Gemini response is shown first, followed by the composited worksheet."
                : "The raw Gemini response is shown first, followed by the composited worksheet and any verification output.";
        for (const artifact of styledArtifacts) {
            styledGrid.appendChild(
                createImageCard({
                    imageUrl: artifactUrl(artifact.relative_path),
                    label: artifact.display_name,
                })
            );
        }
    } else {
        styledSection.hidden = run.lifecycle?.phase === "awaiting_styling_confirmation";
        styledMeta.textContent = state.workflow.stylingPending ? "Pending Response" : "Waiting";
        styledNote.textContent = state.workflow.stylingPending
            ? "Gemini styling is running. The response will appear below this section when it returns."
            : "Styled worksheet responses will appear here after Gemini finishes and verification completes.";
    }
    updateWorkflowActionState();
    renderWorkflowPhaseList();
}

function updateWorkflowActionState({ renderPhaseList = true } = {}) {
    const pending = Boolean(state.workflow.abortController) || state.workflow.stylingPending;
    const hasReview = !document.getElementById("workflow-review-section").hidden;
    const hasRunReview = !document.getElementById("workflow-run-review-section").hidden;
    const canRewriteFromSolution = hasReview && canRegenerateWorkflowPromptFromSolution();
    const regenerateFromSolutionButton = document.getElementById("workflow-regenerate-from-solution-button");
    const workflowGuidanceField = document.getElementById("workflow-image-additional-guidance");
    document.getElementById("workflow-close-button").disabled = pending;
    document.getElementById("workflow-review-prompt").disabled = pending;
    document.getElementById("workflow-review-solution").disabled = pending;
    document.getElementById("workflow-review-actions").hidden = !hasReview;
    document.getElementById("workflow-regenerate-button").hidden = !hasReview || !state.config.gemini.enabled;
    document.getElementById("workflow-regenerate-button").disabled = pending;
    regenerateFromSolutionButton.classList.toggle("is-visible", canRewriteFromSolution);
    regenerateFromSolutionButton.setAttribute("aria-hidden", canRewriteFromSolution ? "false" : "true");
    regenerateFromSolutionButton.disabled = pending || !canRewriteFromSolution;
    document.getElementById("workflow-proceed-button").disabled = pending;
    document.getElementById("workflow-styling-actions").hidden = !hasRunReview || !state.workflow.run?.lifecycle?.can_confirm_styling;
    workflowGuidanceField.disabled = pending || !hasRunReview || !state.workflow.run?.lifecycle?.can_confirm_styling;
    document.getElementById("workflow-confirm-styling-button").disabled = pending;
    document.getElementById("workflow-keep-plain-button").disabled = pending;
    if (renderPhaseList) {
        renderWorkflowPhaseList();
    }
}

function resetWorkflowState() {
    stopWorkflowTimer();
    state.workflow = {
        active: false,
        token: null,
        phase: "idle",
        controls: null,
        draft: null,
        run: null,
        job: null,
        abortController: null,
        startedAt: null,
        timerId: null,
        pollTimerId: null,
        lastJobMessage: "",
        stylingPending: false,
        preserveSolutionRewriteAction: false,
    };
    document.getElementById("workflow-log").innerHTML = "";
    hideWorkflowLockedSummary();
    document.getElementById("workflow-review-section").hidden = true;
    document.getElementById("workflow-run-review-section").hidden = true;
    document.getElementById("workflow-styled-response-section").hidden = true;
    document.getElementById("workflow-review-prompt").value = "";
    document.getElementById("workflow-review-solution").value = "";
    document.getElementById("workflow-run-images").innerHTML = "";
    document.getElementById("workflow-run-meta").textContent = "Run pending";
    document.getElementById("workflow-styled-response-section").hidden = true;
    document.getElementById("workflow-styled-images").innerHTML = "";
    document.getElementById("workflow-styled-response-meta").textContent = "Waiting";
    document.getElementById("workflow-derived-problem-count").value = "0";
    document.getElementById("workflow-derived-total-question-count").value = "0";
    document.getElementById("workflow-derived-problem-count-label").textContent = "Solution Letter Count";
    document.getElementById("workflow-derived-total-question-count-label").textContent = "Derived Question Count";
    document.getElementById("workflow-draft-meta").textContent = "Draft pending";
    document.getElementById("workflow-status-message").textContent = "Waiting to begin.";
    document.getElementById("workflow-status-message").dataset.tone = "neutral";
    document.getElementById("workflow-state-pill").textContent = "Idle";
    document.getElementById("workflow-state-pill").classList.remove("warning");
    document.getElementById("workflow-timer").textContent = "00:00";
    document.getElementById("workflow-parameters").innerHTML = "";
    document.getElementById("workflow-config-section").open = false;
    renderWorkflowPhaseList();
}

function openWorkflowModal(controls, { token = null, createdAt = null, updateHistory = true, replaceHistory = false } = {}) {
    resetWorkflowState();
    state.workflow.active = true;
    state.workflow.token = token;
    state.workflow.controls = controls;
    state.workflow.phase = "starting";
    state.workflow.run = null;
    state.workflow.draft = null;
    renderWorkflowParameters(controls);
    document.getElementById("workflow-subtitle").textContent =
        "The popup owns draft review, approval, generation, and per-job timing.";
    if (createdAt) {
        state.workflow.startedAt = new Date(String(createdAt).replace(" ", "T") + "Z").getTime();
        updateWorkflowTimer();
        state.workflow.timerId = window.setInterval(updateWorkflowTimer, 1000);
    } else {
        startWorkflowTimer();
    }
    setFormDisabled(true);
    document.getElementById("workflow-modal").showModal();
    if (token && updateHistory) {
        setWorkflowUrl(token, { replace: replaceHistory });
    }
    appendWorkflowLog("Workflow opened.");
    renderWorkflowPhaseList();
}

function closeWorkflowModal({ updateHistory = true, replaceHistory = false } = {}) {
    stopWorkflowTimer();
    if (state.workflow.pollTimerId) {
        window.clearTimeout(state.workflow.pollTimerId);
    }
    if (state.workflow.abortController) {
        state.workflow.abortController.abort();
        state.workflow.abortController = null;
    }
    if (document.getElementById("workflow-modal").open) {
        document.getElementById("workflow-modal").close();
    }
    resetWorkflowState();
    setFormDisabled(false);
    if (updateHistory) {
        clearWorkflowUrl({ replace: replaceHistory });
    }
    updateRuntimeMetadata();
}

async function createWorkflowSession(controls) {
    const response = await sendJson("/api/workflow-sessions", "POST", controls);
    return response.workflow_session;
}

async function ensureWorkflowSessionToken(controls) {
    if (state.workflow.token) {
        return state.workflow.token;
    }
    const session = await createWorkflowSession(controls);
    state.workflow.token = session.token;
    setWorkflowUrl(session.token);
    return session.token;
}

async function restoreWorkflowSessionFromToken(token, { replaceHistory = false } = {}) {
    const response = await fetchJson(`/api/workflow-sessions/${encodeURIComponent(token)}`);
    const session = response.workflow_session;
    openWorkflowModal(session.controls, {
        token: session.token,
        createdAt: session.created_at,
        updateHistory: true,
        replaceHistory,
    });
    state.workflow.phase = session.phase || "starting";
    state.workflow.draft = session.draft || null;
    state.workflow.run = session.worksheet_run || null;
    state.workflow.job = session.job || null;
    const isGeneratingWorksheet = session.job && session.job.job_type === "worksheet_generate" && !isTerminalJobStatus(session.job.status);
    const shouldShowRunReview = Boolean(
        session.worksheet_run && (
            session.worksheet_run.lifecycle?.can_confirm_styling
            || (session.job && session.job.job_type === "worksheet_style")
            || ["styled_verified", "styled_failed_plain_retained", "styling_cancelled_plain_retained"].includes(session.worksheet_run.lifecycle?.phase || "")
        )
    );
    if (shouldShowRunReview) {
        renderWorkflowRunReview(session.worksheet_run);
        setWorkflowStatus(
            session.job?.progress_message
                || (session.worksheet_run.lifecycle?.can_confirm_styling
                    ? "Plain worksheet ready. Review it below, then confirm styling or keep the plain worksheet."
                    : "Workflow restored for this worksheet run."),
            session.job && !isTerminalJobStatus(session.job.status) ? "working" : "neutral",
            session.job?.phase_label || runPhaseLabel(session.worksheet_run),
        );
    } else if (isGeneratingWorksheet) {
        renderWorkflowLockedSummary({
            title: "Approved Worksheet Content",
            meta: "Locked",
            note: "The worksheet is generating from this locked prompt and solution.",
            promptText: session.worksheet_run?.prompt_text || session.draft?.prompt_text || "",
            solutionText: session.worksheet_run?.solution_phrase || session.draft?.solution_phrase || "",
        });
        document.getElementById("workflow-review-section").hidden = true;
        document.getElementById("workflow-run-review-section").hidden = true;
        setWorkflowStatus(
            session.job?.progress_message || "Worksheet generation is still running.",
            "working",
            session.job?.phase_label || "Generating Worksheet",
        );
    } else if (session.draft) {
        renderWorkflowDraft(session.draft);
        setWorkflowStatus("Draft restored for review.", "neutral", "Review Draft");
    } else {
        setWorkflowStatus("Workflow restored.", "neutral", "Workflow Restored");
    }
    if (session.job && !isTerminalJobStatus(session.job.status)) {
        if (session.job.job_type === "worksheet_style") {
            state.workflow.stylingPending = true;
        }
        await pollWorkflowJob(session.job.id);
    }
}

async function runWorkflowRequest(path, method, payload, { message, phaseLabel }) {
    const controller = new AbortController();
    state.workflow.abortController = controller;
    setWorkflowStatus(message, "working", phaseLabel);
    appendWorkflowLog(message, "working");
    updateWorkflowActionState();
    try {
        return await sendJson(path, method, payload, { signal: controller.signal });
    } finally {
        state.workflow.abortController = null;
        updateWorkflowActionState();
    }
}

function artifactUrl(relativePath) {
    return `/artifacts/${relativePath}`;
}

function artifactDownloadUrl(artifact) {
    if (artifact?.id) {
        return `/api/artifacts/${artifact.id}/download`;
    }
    return artifactUrl(artifact.relative_path);
}

function artifactRetryRank(artifact) {
    const path = String(artifact?.relative_path || "");
    const match = path.match(/\/retry-(\d+)\//);
    return match ? Number(match[1]) : 0;
}

function compareArtifacts(left, right, priorityMap) {
    const leftPriority = priorityMap[left.artifact_kind] ?? 99;
    const rightPriority = priorityMap[right.artifact_kind] ?? 99;
    if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
    }
    const retryDifference = artifactRetryRank(right) - artifactRetryRank(left);
    if (retryDifference !== 0) {
        return retryDifference;
    }
    return String(left.display_name).localeCompare(String(right.display_name));
}

function modalImageArtifacts(run) {
    const priority = {
        worksheet_preview: 0,
        worksheet_solution: 1,
        worksheet_styled_background: 2,
        worksheet_styled_preview: 3,
        worksheet_styled_overlay_check: 4,
        worksheet_styling_verification: 5,
    };
    return (run.artifacts || []).filter((artifact) =>
        artifact.output_format === "png" && (
            [
                "worksheet_preview",
                "worksheet_solution",
                "worksheet_styled_preview",
                "worksheet_styled_background",
                "worksheet_styled_overlay_check",
            ].includes(artifact.artifact_kind) ||
            (artifact.artifact_kind === "worksheet_styling_verification" && shouldShowDiagnosticArtifacts(run))
        )
    ).sort((left, right) => compareArtifacts(left, right, priority));
}

function modalDownloadArtifacts(run) {
    return (run.artifacts || []).filter((artifact) => {
        if (["worksheet_semantic_foreground", "worksheet_styled_overlay_check"].includes(artifact.artifact_kind)) {
            return false;
        }
        if (["worksheet_styling_debug", "worksheet_styling_verification"].includes(artifact.artifact_kind)) {
            return shouldShowDiagnosticArtifacts(run);
        }
        return true;
    });
}

function renderGallery(data) {
    const grid = document.getElementById("gallery-grid");
    const stats = document.getElementById("gallery-stats");
    const loadMoreButton = document.getElementById("gallery-load-more-button");
    state.currentGalleryItems = data.items;
    stats.innerHTML = "";
    if (state.filters.offset === 0) {
        grid.innerHTML = "";
    }

    const statItems = isDebugUiEnabled()
        ? [
            `Runs: ${data.counts.worksheet_runs}`,
            `Artifacts: ${data.counts.artifacts}`,
            `Jobs: ${data.counts.jobs}`,
            `Drafts: ${data.counts.reward_content_drafts}`,
            `Showing ${data.pagination.returned + data.pagination.offset} of ${data.pagination.total}`,
        ]
        : [
            `${data.pagination.total} matching worksheets`,
        ];
    for (const label of statItems) {
        const node = document.createElement("span");
        node.textContent = label;
        stats.appendChild(node);
    }

    if (!data.items.length) {
        const template = document.getElementById("empty-gallery-template");
        const fragment = template.content.cloneNode(true);
        const message = fragment.querySelector("#empty-gallery-message");
        if (message) {
            message.textContent = data.empty_state;
        }
        grid.appendChild(fragment);
        loadMoreButton.hidden = true;
        return;
    }

    for (const item of data.items) {
        const card = document.createElement("article");
        card.className = "tile shell-card";
        card.tabIndex = 0;
        const galleryThumbnailPath = item.styling?.styled_thumbnail_path || item.thumbnail_path;
        const thumbnailWrapper = document.createElement(galleryThumbnailPath ? "img" : "div");
        thumbnailWrapper.className = galleryThumbnailPath ? "tile-thumb" : "tile-thumb tile-thumb-empty";
        if (galleryThumbnailPath) {
            thumbnailWrapper.src = artifactUrl(galleryThumbnailPath);
            thumbnailWrapper.alt = `${item.title} preview`;
        } else {
            thumbnailWrapper.textContent = "No preview yet";
        }

        const body = document.createElement("div");
        body.className = "tile-body";
        appendTextElement(body, "h3", item.title);
        appendTextElement(body, "p", customerRunSummary(item), "muted tile-copy");
        if (isDebugUiEnabled()) {
            const tagValues = [
                item.learner_band.replaceAll("_", " "),
                item.reveal_mode.replaceAll("_", " "),
                item.skill_profile.replaceAll("_", " "),
                runPhaseLabel(item, item.status),
                item.styling?.requested ? `style ${item.styling.style_name}` : "base render",
                item.styling?.status ? `styling ${String(item.styling.status).replaceAll("_", " ")}` : "styling n/a",
            ];
            const tags = document.createElement("div");
            tags.className = "tile-tags";
            for (const value of tagValues) {
                tags.appendChild(createMiniPill(value));
            }
            body.appendChild(tags);

            const detailList = document.createElement("ul");
            const detailItems = [
                `Status: ${runPhaseLabel(item, item.status)}`,
                `Theme: ${item.theme || "n/a"}`,
                `Solution: ${item.solution_phrase || "n/a"}`,
                `Image styling: ${item.styling?.status || "not requested"}`,
                `Verification: ${item.styling?.verification_status || "n/a"}`,
            ];
            for (const value of detailItems) {
                appendTextElement(detailList, "li", value);
            }
            body.appendChild(detailList);
        }

        const footer = document.createElement("div");
        footer.className = "tile-footer";
        footer.appendChild(createMiniPill(runPhaseLabel(item, item.status)));
        const footerMeta = document.createElement("div");
        footerMeta.className = "tile-footer-meta";
        appendTextElement(footerMeta, "span", `Created ${formatRunTimestamp(item.created_at)}`, "tile-timestamp");
        appendTextElement(footerMeta, "span", "Open worksheet", "tile-open-hint");
        footer.appendChild(footerMeta);
        body.appendChild(footer);

        card.appendChild(thumbnailWrapper);
        card.appendChild(body);
        card.addEventListener("click", () => openRunModal(item.id));
        card.addEventListener("keypress", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                openRunModal(item.id);
            }
        });
        grid.appendChild(card);
    }
    loadMoreButton.hidden = !data.pagination.has_more;
}

function renderModal(run) {
    state.currentModalRun = run;
    const taskToken = workflowTaskToken(run);
    document.getElementById("modal-title").textContent = run.title;
    document.getElementById("modal-subtitle").textContent =
        `${taskToken ? `Task ${taskToken}` : `Run #${run.id}`} · ${run.learner_band.replaceAll("_", " ")} · ${run.reveal_mode.replaceAll("_", " ")} · ${runPhaseLabel(run, run.status)}`;
    document.getElementById("modal-run-state-pill").textContent = runPhaseLabel(run, run.status);
    document.getElementById("modal-run-state-note").textContent =
        run.lifecycle?.phase === "awaiting_styling_confirmation"
            ? "The plain worksheet is ready. Review it here and decide whether to continue into Gemini styling."
            : run.lifecycle?.phase === "styled_verified"
                ? "This run is complete. Compare the plain worksheet, the raw Gemini response, and the verified styled result here."
                : run.lifecycle?.phase === "styling_cancelled_plain_retained"
                    ? "Styling was cancelled after plain review. This detail view remains the canonical source for the plain worksheet artifacts."
                    : run.lifecycle?.phase === "styled_failed_plain_retained"
                        ? "Styling failed and the plain worksheet was retained. Review the debug artifacts here or retry styling from this run."
                        : "This detail view is the canonical place to inspect the run’s plain, styled, and verification artifacts.";
    document.getElementById("modal-prompt").textContent = run.prompt_text || "No prompt stored.";
    document.getElementById("modal-solution").textContent = run.solution_phrase || "No solution stored.";

    const summary = document.getElementById("modal-summary");
    summary.innerHTML = "";
    const selectedSkillSummary = Array.isArray(run.parameters?.selected_skills) && run.parameters.selected_skills.length
        ? run.parameters.selected_skills.map((item) => `${item.skill} (${item.difficulty_minimum}-${item.difficulty_maximum})`).join(", ")
        : run.skill_profile.replaceAll("_", " ");
    const summaryItems = [
        `Status: ${runPhaseLabel(run, run.status)}`,
        `${taskToken ? "Task" : "Run"}: ${taskToken || run.id}`,
        `Created: ${formatRunTimestamp(run.created_at)}`,
        `Learner band: ${run.learner_band.replaceAll("_", " ")}`,
        `Reveal mode: ${run.reveal_mode.replaceAll("_", " ")}`,
        `Challenge types: ${selectedSkillSummary}`,
    ];
    if (run.theme) {
        summaryItems.push(`Theme: ${run.theme}`);
    }
    if (run.styling?.requested) {
        summaryItems.push(`Image styling: ${run.styling.style_name}`);
    }
    for (const value of summaryItems) {
        appendTextElement(summary, "li", value);
    }

    const meta = document.getElementById("modal-meta");
    meta.innerHTML = "";
    if (isDebugUiEnabled()) {
        for (const [key, value] of Object.entries(run.parameters || {})) {
            const node = document.createElement("li");
            node.textContent = `${key}: ${value}`;
            meta.appendChild(node);
        }
        if (run.styling) {
            for (const [key, value] of Object.entries(run.styling)) {
                const node = document.createElement("li");
                node.textContent = `styling.${key}: ${value}`;
                meta.appendChild(node);
            }
        }
    }

    const stylingSection = document.getElementById("modal-styling-decision-section");
    const stylingStatusPill = document.getElementById("modal-styling-status-pill");
    const stylingNote = document.getElementById("modal-styling-note");
    const guidanceField = document.getElementById("modal-image-guidance-field");
    const guidanceInput = document.getElementById("modal-image-additional-guidance");
    const retryButton = document.getElementById("modal-retry-styling-button");
    const retryGenerationButton = document.getElementById("modal-retry-generation-button");
    const stylingStatus = run.styling?.status || "not_requested";
    const lifecyclePhase = run.lifecycle?.phase || "plain_worksheet_ready";
    stylingSection.hidden = !run.styling?.requested;
    stylingStatusPill.textContent = runPhaseLabel(run, stylingStatus.replaceAll("_", " "));
    guidanceField.hidden = !run.lifecycle?.can_confirm_styling;
    guidanceInput.value = run.parameters?.image_additional_guidance || "";
    guidanceInput.disabled = !run.lifecycle?.can_confirm_styling;
    updateStyleGuidanceStatus({
        previewId: "modal-image-guidance-preview",
        countId: "modal-image-guidance-count",
        guidance: guidanceInput.value,
    });
    retryButton.hidden = true;
    retryGenerationButton.hidden = !run.lifecycle?.can_retry_generation;
    if (run.lifecycle?.can_confirm_styling) {
        stylingNote.textContent =
            "Review the plain worksheet first. Then confirm whether Gemini should generate a styled version, or keep the plain worksheet only.";
        document.getElementById("modal-confirm-styling-button").hidden = false;
        document.getElementById("modal-cancel-styling-button").hidden = false;
    } else if (lifecyclePhase === "styling_running" || lifecyclePhase === "styling_queued") {
        stylingNote.textContent =
            stylingStatus === "retry_pending_styling" || stylingStatus === "retry_in_progress"
                ? "Styling retry is in progress. The original worksheet remains available while Gemini styling, compositing, and verification continue."
                : "Styling is in progress. The original worksheet remains available while Gemini styling, compositing, and verification continue.";
        document.getElementById("modal-confirm-styling-button").hidden = true;
        document.getElementById("modal-cancel-styling-button").hidden = true;
    } else if (lifecyclePhase === "styled_verified") {
        stylingNote.textContent =
            "Styled worksheet artifacts passed verification and are available alongside the original worksheet.";
        document.getElementById("modal-confirm-styling-button").hidden = true;
        document.getElementById("modal-cancel-styling-button").hidden = true;
    } else if (lifecyclePhase === "styled_failed_plain_retained") {
        stylingNote.textContent =
            "Styling did not complete cleanly. The base worksheet was retained and debug artifacts were saved for review. You can retry styling from this run.";
        document.getElementById("modal-confirm-styling-button").hidden = true;
        document.getElementById("modal-cancel-styling-button").hidden = true;
        retryButton.hidden = !run.lifecycle?.can_retry_styling;
    } else if (lifecyclePhase === "styling_cancelled_plain_retained") {
        stylingNote.textContent =
            "Styling was cancelled after plain-worksheet review. The base worksheet remains the final artifact for this run.";
        document.getElementById("modal-confirm-styling-button").hidden = true;
        document.getElementById("modal-cancel-styling-button").hidden = true;
    } else {
        stylingNote.textContent = "This run does not currently have an active styling decision to make.";
        document.getElementById("modal-confirm-styling-button").hidden = true;
        document.getElementById("modal-cancel-styling-button").hidden = true;
    }

    const downloads = document.getElementById("modal-downloads");
    downloads.innerHTML = "";
    for (const artifact of modalDownloadArtifacts(run)) {
        const link = document.createElement("a");
        link.href = artifactDownloadUrl(artifact);
        link.textContent = `${artifact.display_name}`;
        link.className = "download-link";
        downloads.appendChild(link);
    }

    const images = document.getElementById("modal-images");
    images.innerHTML = "";
    for (const artifact of modalImageArtifacts(run)) {
        images.appendChild(
            createImageCard({
                imageUrl: artifactUrl(artifact.relative_path),
                label: artifact.display_name,
            })
        );
    }

    document.getElementById("run-modal").showModal();
}

function openImagePreview(imageUrl, title) {
    const modal = document.getElementById("image-preview-modal");
    const image = document.getElementById("image-preview-display");
    const heading = document.getElementById("image-preview-title");
    image.src = imageUrl;
    image.alt = title;
    heading.textContent = title;
    modal.showModal();
}

function closeImagePreview() {
    const modal = document.getElementById("image-preview-modal");
    const image = document.getElementById("image-preview-display");
    image.src = "";
    image.alt = "";
    modal.close();
}

async function openRunModal(runId, { updateHistory = true, replaceHistory = false } = {}) {
    const response = await fetchJson(`/api/worksheet-runs/${runId}`);
    renderModal(response.worksheet_run);
    if (updateHistory) {
        setRunDetailUrl(runId, { replace: replaceHistory });
    }
}

function closeRunModal({ updateHistory = true, replaceHistory = false } = {}) {
    if (document.getElementById("run-modal").open) {
        document.getElementById("run-modal").close();
    }
    state.currentModalRun = null;
    if (updateHistory) {
        clearRunDetailUrl({ replace: replaceHistory });
    }
}

async function syncRunModalToLocation({ replaceHistory = false } = {}) {
    const runId = requestedRunIdFromUrl();
    if (runId) {
        if (!state.currentModalRun || state.currentModalRun.id !== runId || !document.getElementById("run-modal").open) {
            await openRunModal(runId, { updateHistory: false, replaceHistory });
        }
        return;
    }
    if (document.getElementById("run-modal").open) {
        closeRunModal({ updateHistory: false, replaceHistory });
    }
}

async function syncWorkflowModalToLocation({ replaceHistory = false } = {}) {
    const token = requestedWorkflowTokenFromUrl();
    if (token) {
        if (!state.workflow.active || state.workflow.token !== token || !document.getElementById("workflow-modal").open) {
            if (document.getElementById("run-modal").open) {
                closeRunModal({ updateHistory: false, replaceHistory });
            }
            await restoreWorkflowSessionFromToken(token, { replaceHistory });
        }
        return true;
    }
    if (document.getElementById("workflow-modal").open) {
        closeWorkflowModal({ updateHistory: false, replaceHistory });
    }
    return false;
}

async function refreshGallery() {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(state.filters)) {
        if (value !== "" && value !== null && value !== undefined) {
            params.set(key, value);
        }
    }
    const query = params.toString();
    const data = await fetchJson(`/api/gallery${query ? `?${query}` : ""}`);
    renderGallery(data);
}

async function refreshMaintenance() {
    if (!isDebugUiEnabled()) {
        return;
    }
    const snapshot = await fetchJson("/api/maintenance");
    renderMaintenanceMetadata(snapshot);
}

async function persistWorkflowDraftEdits() {
    if (!state.workflow.draft) {
        return null;
    }
    const controls = state.workflow.controls;
    const payload = {
        workflow_token: state.workflow.token,
        prompt_text: document.getElementById("workflow-review-prompt").value,
        solution_phrase: document.getElementById("workflow-review-solution").value,
        learner_band: controls.learner_band,
        theme: controls.theme,
        style: controls.style,
        language: controls.language,
        difficulty_maximum: controls.difficulty_maximum,
    };
    const changed =
        payload.prompt_text !== state.workflow.draft.prompt_text ||
        payload.solution_phrase !== state.workflow.draft.solution_phrase ||
        payload.learner_band !== state.workflow.draft.learner_band ||
        (payload.theme || null) !== (state.workflow.draft.theme || null) ||
        payload.style !== (state.workflow.draft.style || "riddle") ||
        payload.language !== state.workflow.draft.language;
    if (!changed) {
        return state.workflow.draft;
    }
    const response = await runWorkflowRequest(
        `/api/reward-content/${state.workflow.draft.id}`,
        "PATCH",
        payload,
        { message: "Saving review edits.", phaseLabel: "Saving Review" },
    );
    renderDraft(response.draft);
    state.workflow.draft = response.draft;
    appendWorkflowLog("Review edits saved.", "success");
    document.getElementById("workflow-draft-meta").textContent =
        `Draft #${response.draft.id} · ${response.draft.approval_state} · ${response.draft.source}`;
    return response.draft;
}

async function createManualDraftFromWorkflow() {
    const controls = state.workflow.controls;
    const response = await runWorkflowRequest("/api/reward-content/direct", "POST", {
        workflow_token: state.workflow.token,
        theme: controls.theme,
        learner_band: controls.learner_band,
        style: controls.style,
        language: controls.language,
        difficulty_maximum: controls.difficulty_maximum,
        prompt_text: document.getElementById("workflow-review-prompt").value,
        solution_phrase: document.getElementById("workflow-review-solution").value,
    }, {
        message: "Creating manual draft from review fields.",
        phaseLabel: "Creating Draft",
    });
    renderWorkflowDraft(response.draft);
    appendWorkflowLog(`Manual draft #${response.draft.id} created.`, "success");
    return response.draft;
}

async function generateWorkflowDraft({ regenerate = false } = {}) {
    if (!state.config.gemini.enabled) {
        renderManualReview();
        setWorkflowStatus("Gemini is unavailable. Review the content manually and proceed when ready.", "neutral", "Manual Review");
        appendWorkflowLog("Gemini unavailable, entering manual review mode.");
        return;
    }
    const controls = state.workflow.controls;
    const path = regenerate && state.workflow.draft
        ? `/api/reward-content/${state.workflow.draft.id}/regenerate`
        : "/api/reward-content/generate";
    const response = await runWorkflowRequest(path, "POST", { ...controls, workflow_token: state.workflow.token }, {
        message: regenerate ? "Regenerating Gemini riddle." : "Generating Gemini riddle.",
        phaseLabel: regenerate ? "Regenerating Riddle" : "Generating Riddle",
    });
    renderWorkflowDraft(response.draft);
    setWorkflowStatus("Riddle ready for review. Edit it, regenerate it, or render the worksheet.", "success", "Review Riddle");
    appendWorkflowLog(
        regenerate
            ? `Riddle #${response.draft.id} regenerated and ready for review.`
            : `Riddle #${response.draft.id} generated and ready for review.`,
        "success",
    );
}

async function regenerateWorkflowPromptFromSolution() {
    if (!state.workflow.draft) {
        return;
    }
    const controls = state.workflow.controls;
    const response = await runWorkflowRequest(
        `/api/reward-content/${state.workflow.draft.id}/regenerate-from-solution`,
        "POST",
        {
            workflow_token: state.workflow.token,
            theme: controls.theme,
            learner_band: controls.learner_band,
            style: controls.style,
            language: controls.language,
            reveal_mode: controls.reveal_mode,
            color_picture_source: controls.color_picture_source,
            color_picture_preset: controls.color_picture_preset,
            difficulty_maximum: controls.difficulty_maximum,
            solution_phrase: document.getElementById("workflow-review-solution").value,
        },
        {
            message: "Generating a new clue for the edited solution.",
            phaseLabel: "Rewriting Clue",
        },
    );
    renderWorkflowDraft(response.draft, { preserveSolutionRewriteAction: true });
    setWorkflowStatus("New clue ready for review.", "success", "Review Riddle");
    appendWorkflowLog(`Riddle #${response.draft.id} rewritten for solution "${response.draft.solution_phrase}".`, "success");
}

async function approveWorkflowDraft(draftId) {
    state.workflow.phase = "approving_draft";
    renderWorkflowPhaseList();
    const response = await runWorkflowRequest(`/api/reward-content/${draftId}/approve`, "POST", {
        workflow_token: state.workflow.token,
        learner_band: state.workflow.controls.learner_band,
    }, {
        message: "Approving reviewed riddle.",
        phaseLabel: "Approving Riddle",
    });
    renderDraft(response.draft);
    state.workflow.draft = response.draft;
    appendWorkflowLog(`Riddle #${response.draft.id} approved.`, "success");
    return response.draft;
}

async function generateWorksheetFromWorkflow(draftId) {
    const controls = state.workflow.controls;
    const response = await runWorkflowRequest("/api/worksheets/generate", "POST", {
        workflow_token: state.workflow.token,
        draft_id: draftId,
        learner_band: controls.learner_band,
        reveal_mode: controls.reveal_mode,
        skill_profile: controls.skill_profile,
        difficulty_minimum: controls.difficulty_minimum,
        difficulty_maximum: controls.difficulty_maximum,
        theme: controls.theme,
        seed: controls.seed,
        decoy_percentage: controls.decoy_percentage,
        color_picture_source: controls.color_picture_source,
        color_picture_preset: controls.color_picture_preset,
        apply_image_styling: controls.apply_image_styling,
        image_style_name: controls.image_style_name,
        image_color_mode: controls.image_color_mode,
        image_ink_saver: controls.image_ink_saver,
        image_additional_guidance: controls.image_additional_guidance,
    }, {
        message: "Rendering worksheet artifacts and saving them to persistent storage.",
        phaseLabel: "Rendering Worksheet",
    });
    state.workflow.run = response.worksheet_run;
    state.workflow.job = response.job;
    state.workflow.phase = response.job?.phase || state.workflow.phase;
    await refreshGallery();
    setWorkflowStatus(
        response.job?.progress_message || `Worksheet run #${response.worksheet_run.id} is queued for generation.`,
        "working",
        response.job?.phase_label || "Worksheet Rendering",
    );
    appendWorkflowLog(
        response.job?.progress_message || `Worksheet run #${response.worksheet_run.id} queued for generation.`,
        "working",
    );
    updateWorkflowActionState();
    return response;
}

async function pollWorkflowJob(jobId, { onCompleted } = {}) {
    const poll = async () => {
        try {
            const response = await fetchJson(`/api/jobs/${jobId}?wait_seconds=5`);
            const job = response.job;
            state.workflow.job = job;
            state.workflow.phase = job.phase || state.workflow.phase;
            const phaseLabel = job.phase_label || jobPhaseLabel(job.phase, job.job_type === "worksheet_generate" ? "Generation Job" : "Styling Job");
            if (job.progress_message && job.progress_message !== state.workflow.lastJobMessage) {
                appendWorkflowLog(job.progress_message, job.status === "failed" ? "error" : "working");
                state.workflow.lastJobMessage = job.progress_message;
                setWorkflowStatus(job.progress_message, job.status === "failed" ? "error" : "working", phaseLabel);
            }
            if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
                state.workflow.stylingPending = false;
                const runResponse = job.worksheet_run_id
                    ? await fetchJson(`/api/worksheet-runs/${job.worksheet_run_id}`)
                    : { worksheet_run: null };
                state.workflow.run = runResponse.worksheet_run;
                await refreshGallery();
                if (job.status === "cancelled") {
                    if (job.job_type === "worksheet_style" && runResponse.worksheet_run) {
                        appendWorkflowLog(
                            `Styling for worksheet run #${runResponse.worksheet_run.id} was cancelled. Opening the plain run detail.`,
                            "error",
                        );
                        closeWorkflowModal();
                        await openRunModal(runResponse.worksheet_run.id);
                    } else {
                        appendWorkflowLog("Worksheet generation was cancelled.", "error");
                        closeWorkflowModal();
                    }
                    return;
                }
                if (job.job_type === "worksheet_generate") {
                    if (job.status === "completed") {
                        if (state.workflow.controls.apply_image_styling && runResponse.worksheet_run.styling?.requested) {
                            state.workflow.phase = "review_plain_run";
                            renderWorkflowRunReview(runResponse.worksheet_run);
                            setWorkflowStatus(
                                "Plain worksheet ready. Review it below, then confirm styling or keep the plain worksheet.",
                                "success",
                                "Review Plain Worksheet",
                            );
                            appendWorkflowLog(`Worksheet run #${runResponse.worksheet_run.id} is ready for plain review.`, "success");
                        } else {
                            appendWorkflowLog(`Worksheet run #${runResponse.worksheet_run.id} is ready. Opening worksheet viewer.`, "success");
                            closeWorkflowModal();
                            await openRunModal(runResponse.worksheet_run.id);
                        }
                    } else {
                        setWorkflowStatus(
                            "Worksheet generation failed. Review the error log and adjust the settings before trying again.",
                            "error",
                            "Generation Failed",
                        );
                        appendWorkflowLog(`Worksheet run #${runResponse.worksheet_run.id} failed to generate.`, "error");
                    }
                } else {
                    renderWorkflowRunReview(runResponse.worksheet_run);
                    if (job.status === "completed") {
                        setWorkflowStatus("Styled worksheet artifacts are ready. Opening the run detail view.", "success", "Styled Complete");
                        appendWorkflowLog(`Styled worksheet run #${runResponse.worksheet_run.id} is ready. Opening run detail.`, "success");
                        closeWorkflowModal();
                        await openRunModal(runResponse.worksheet_run.id);
                        return;
                    } else {
                        appendWorkflowLog(
                            `Styled worksheet run #${runResponse.worksheet_run.id} finished with a failure state. Opening run detail with plain artifacts retained.`,
                            "error",
                        );
                        closeWorkflowModal();
                        await openRunModal(runResponse.worksheet_run.id);
                        return;
                    }
                }
                if (onCompleted) {
                    onCompleted(job, runResponse.worksheet_run);
                }
                updateWorkflowActionState();
                return;
            }
            state.workflow.pollTimerId = window.setTimeout(poll, 1000);
        } catch (error) {
            state.workflow.stylingPending = false;
            setWorkflowStatus(error.message, "error", "Needs Attention");
            appendWorkflowLog(error.message, "error");
            updateWorkflowActionState();
        }
    };
    await poll();
}

async function onWorkflowSubmit(event) {
    event.preventDefault();
    if (state.workflow.active) {
        return;
    }
    const controls = collectControlPayload();
    const validationError = validateControlPayloadPreflight(controls);
    if (validationError) {
        setFormValidationMessage(validationError);
        setStatus("Fix the worksheet settings before starting the workflow.", "error");
        return;
    }
    setFormValidationMessage("");
    const session = await createWorkflowSession(controls);
    openWorkflowModal(controls, { token: session.token, createdAt: session.created_at });
    try {
        await generateWorkflowDraft();
    } catch (error) {
        if (error.name === "AbortError") {
            closeWorkflowModal();
            return;
        }
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
        updateWorkflowActionState();
    }
}

async function onWorkflowProceed() {
    try {
        let draft = state.workflow.draft;
        if (draft) {
            draft = await persistWorkflowDraftEdits();
        } else {
            draft = await createManualDraftFromWorkflow();
        }
            renderWorkflowLockedSummary({
                title: "Approved Worksheet Content",
                meta: "Locked",
                note: "The worksheet is now being rendered from this locked riddle and solution.",
                promptText: draft.prompt_text,
                solutionText: draft.solution_phrase,
            });
        document.getElementById("workflow-review-section").hidden = true;
        const approved = await approveWorkflowDraft(draft.id);
        const generation = await generateWorksheetFromWorkflow(approved.id);
        if (generation.job?.status === "completed") {
            await pollWorkflowJob(generation.job.id);
            return;
        }
        await pollWorkflowJob(generation.job.id);
    } catch (error) {
        if (error.name === "AbortError") {
            closeWorkflowModal();
            return;
        }
        if (!state.workflow.run) {
            hideWorkflowLockedSummary();
            if (state.workflow.draft) {
                renderWorkflowDraft(state.workflow.draft);
            } else {
                renderManualReview();
            }
        }
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
        updateWorkflowActionState();
    }
}

async function onConfirmStylingDecision() {
    try {
        const imageAdditionalGuidance = currentModalStylingGuidance();
        const controls = workflowControlsFromRun({
            ...state.currentModalRun,
            parameters: {
                ...(state.currentModalRun.parameters || {}),
                image_additional_guidance: imageAdditionalGuidance,
            },
        });
        await ensureWorkflowSessionToken(controls);
        const response = await sendJson(
            `/api/worksheet-runs/${state.currentModalRun.id}/styling-decision`,
            "POST",
            {
                decision: "confirm",
                workflow_token: state.workflow.token,
                image_additional_guidance: imageAdditionalGuidance,
            },
        );
        setStatus("Styling confirmed for this worksheet run.", "neutral");
        if (response.job) {
            const run = response.worksheet_run;
            closeRunModal();
            openWorkflowModal(workflowControlsFromRun(run), { token: state.workflow.token });
            state.workflow.run = run;
            state.workflow.job = response.job;
            renderWorkflowRunReview(run);
            setWorkflowStatus("Styling job started.", "working", "Styling Job");
            appendWorkflowLog(`Styling job #${response.job.id} started for worksheet run #${run.id}.`, "working");
            await pollWorkflowJob(response.job.id);
        }
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onCancelStylingDecision() {
    try {
        await sendJson(
            `/api/worksheet-runs/${state.currentModalRun.id}/styling-decision`,
            "POST",
            { decision: "cancel", workflow_token: state.workflow.token },
        );
        setStatus("Styling cancelled. The plain worksheet remains available.", "neutral");
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onRetryStylingDecision() {
    if (!state.currentModalRun) {
        return;
    }
    try {
        await ensureWorkflowSessionToken(workflowControlsFromRun(state.currentModalRun));
        const response = await sendJson(
            `/api/worksheet-runs/${state.currentModalRun.id}/styling-retry`,
            "POST",
            { workflow_token: state.workflow.token },
        );
        const run = response.worksheet_run;
        closeRunModal();
        openWorkflowModal(workflowControlsFromRun(run), { token: state.workflow.token });
        state.workflow.run = run;
        state.workflow.job = response.job;
        state.workflow.stylingPending = true;
        renderWorkflowRunReview(run);
        setWorkflowStatus("Styling retry started.", "working", response.job?.phase_label || "Styling Retry");
        appendWorkflowLog(`Styling retry job #${response.job.id} started for worksheet run #${run.id}.`, "working");
        await pollWorkflowJob(response.job.id);
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onRetryGenerationDecision() {
    if (!state.currentModalRun) {
        return;
    }
    try {
        await ensureWorkflowSessionToken(workflowControlsFromRun(state.currentModalRun));
        const sourceRunId = state.currentModalRun.id;
        const response = await sendJson(
            `/api/worksheet-runs/${sourceRunId}/retry-generation`,
            "POST",
            { workflow_token: state.workflow.token },
        );
        const run = response.worksheet_run;
        closeRunModal();
        openWorkflowModal(workflowControlsFromRun(run), { token: state.workflow.token });
        state.workflow.run = run;
        state.workflow.job = response.job;
        state.workflow.phase = response.job?.phase || state.workflow.phase;
        setWorkflowStatus("Worksheet regeneration started.", "working", response.job?.phase_label || "Worksheet Generation");
        appendWorkflowLog(`Worksheet regeneration job #${response.job.id} started from run #${sourceRunId}.`, "working");
        await pollWorkflowJob(response.job.id);
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onMaintenanceRefresh() {
    try {
        await refreshMaintenance();
        setStatus("Maintenance summary refreshed.", "neutral");
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onMaintenancePrune() {
    try {
        const response = await sendJson("/api/maintenance/prune-artifacts", "POST", {});
        renderMaintenanceMetadata(response.post_prune_snapshot);
        setStatus(`Pruned ${response.removed_run_directory_count} orphan run directories.`, "neutral");
        await refreshGallery();
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onMaintenanceVacuum() {
    try {
        const response = await sendJson("/api/maintenance/vacuum", "POST", {});
        renderMaintenanceMetadata(response.snapshot);
        setStatus("Database maintenance completed.", "neutral");
    } catch (error) {
        console.error(error);
        setStatus(error.message, "error");
    }
}

async function onWorkflowConfirmStyling() {
    if (!state.workflow.run) {
        return;
    }
    try {
        const imageAdditionalGuidance = currentWorkflowStylingGuidance();
        if (state.workflow.controls) {
            state.workflow.controls = {
                ...state.workflow.controls,
                image_additional_guidance: imageAdditionalGuidance,
            };
            renderWorkflowParameters(state.workflow.controls);
        }
        state.workflow.stylingPending = true;
        state.workflow.run = {
            ...state.workflow.run,
            parameters: {
                ...(state.workflow.run.parameters || {}),
                image_additional_guidance: imageAdditionalGuidance,
            },
            lifecycle: {
                ...(state.workflow.run.lifecycle || {}),
                phase: "styling_queued",
                can_confirm_styling: false,
                label: state.config?.job_tracking?.run_phase_catalog?.styling_queued || "Styling Queued",
            },
        };
        renderWorkflowRunReview(state.workflow.run);
        setWorkflowStatus("Confirming styling and starting the Gemini styling job.", "working", "Starting Styling");
        appendWorkflowLog(`Confirming styling for worksheet run #${state.workflow.run.id}.`, "working");
        const response = await runWorkflowRequest(
            `/api/worksheet-runs/${state.workflow.run.id}/styling-decision`,
            "POST",
            {
                decision: "confirm",
                workflow_token: state.workflow.token,
                image_additional_guidance: imageAdditionalGuidance,
            },
            {
                message: "Starting Gemini styling after plain worksheet review.",
                phaseLabel: "Starting Styling",
            },
        );
        state.workflow.controls = workflowControlsFromRun(response.worksheet_run);
        state.workflow.run = response.worksheet_run;
        state.workflow.job = response.job;
        renderWorkflowRunReview(response.worksheet_run);
        appendWorkflowLog(`Styling job #${response.job.id} started.`, "working");
        await pollWorkflowJob(response.job.id);
    } catch (error) {
        state.workflow.stylingPending = false;
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
        updateWorkflowActionState();
    }
}

async function onWorkflowKeepPlain() {
    if (!state.workflow.run) {
        return;
    }
    try {
        setWorkflowStatus("Keeping the plain worksheet and cancelling styling.", "working", "Keep Plain Worksheet");
        const response = await runWorkflowRequest(
            `/api/worksheet-runs/${state.workflow.run.id}/styling-decision`,
            "POST",
            { decision: "cancel", workflow_token: state.workflow.token },
            {
                message: "Keeping the plain worksheet and cancelling styling.",
                phaseLabel: "Keep Plain Worksheet",
            },
        );
        state.workflow.run = response.worksheet_run;
        renderWorkflowRunReview(response.worksheet_run);
        await refreshGallery();
        appendWorkflowLog(`Styling cancelled for worksheet run #${response.worksheet_run.id}. Opening run detail.`, "success");
        closeWorkflowModal();
        await openRunModal(response.worksheet_run.id);
    } catch (error) {
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
    }
}

async function onWorkflowRegenerate() {
    try {
        await generateWorkflowDraft({ regenerate: true });
    } catch (error) {
        if (error.name === "AbortError") {
            closeWorkflowModal();
            return;
        }
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
        updateWorkflowActionState();
    }
}

async function onWorkflowCancel() {
    const activeBackgroundJob = state.workflow.job && !isTerminalJobStatus(state.workflow.job.status)
        && ["worksheet_generate", "worksheet_style"].includes(state.workflow.job.job_type);
    if (!activeBackgroundJob) {
        closeWorkflowModal();
        return;
    }
    try {
        setWorkflowStatus("Cancelling the active workflow job.", "working", "Cancelling");
        appendWorkflowLog(`Cancelling job #${state.workflow.job.id}.`, "working");
        const response = await sendJson(`/api/jobs/${state.workflow.job.id}/cancel`, "POST", {});
        state.workflow.job = response.job;
        state.workflow.run = response.worksheet_run || state.workflow.run;
        await refreshGallery();
        if (response.worksheet_run && response.job.job_type === "worksheet_style") {
            closeWorkflowModal();
            await openRunModal(response.worksheet_run.id);
            setStatus("Styling cancelled. The plain worksheet remains available.", "neutral");
            return;
        }
        closeWorkflowModal();
        setStatus("Workflow generation cancelled.", "neutral");
    } catch (error) {
        setWorkflowStatus(error.message, "error", "Needs Attention");
        appendWorkflowLog(error.message, "error");
        updateWorkflowActionState();
    }
}

function onWorkflowClose() {
    closeWorkflowModal();
}

function attachEventHandlers() {
    const form = document.getElementById("worksheet-config-form");
    document.getElementById("worksheet-config-form").addEventListener("submit", (event) => {
        onWorkflowSubmit(event).catch((error) => {
            console.error(error);
            setStatus(error.message, "error");
        });
    });
    document.getElementById("workflow-review-solution").addEventListener("input", () => {
        updateWorkflowDerivedProblemCount();
        updateWorkflowActionState({ renderPhaseList: false });
    });
    document.getElementById("image-additional-guidance").addEventListener("input", () => {
        updateStyleGuidanceStatus({
            countId: "image-additional-guidance-count",
            guidance: document.getElementById("image-additional-guidance").value,
        });
    });
    document.getElementById("workflow-image-additional-guidance").addEventListener("input", () => {
        const guidance = currentWorkflowStylingGuidance();
        updateStyleGuidanceStatus({
            previewId: "workflow-image-guidance-preview",
            countId: "workflow-image-guidance-count",
            guidance: document.getElementById("workflow-image-additional-guidance").value,
        });
        if (state.workflow.controls) {
            state.workflow.controls = {
                ...state.workflow.controls,
                image_additional_guidance: guidance,
            };
            renderWorkflowParameters(state.workflow.controls);
        }
    });
    document.getElementById("modal-image-additional-guidance").addEventListener("input", () => {
        const guidance = currentModalStylingGuidance();
        updateStyleGuidanceStatus({
            previewId: "modal-image-guidance-preview",
            countId: "modal-image-guidance-count",
            guidance: document.getElementById("modal-image-additional-guidance").value,
        });
        if (state.currentModalRun) {
            state.currentModalRun = {
                ...state.currentModalRun,
                parameters: {
                    ...(state.currentModalRun.parameters || {}),
                    image_additional_guidance: guidance,
                },
            };
        }
    });
    document.getElementById("workflow-phase-list").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-workflow-action]");
        if (!button) {
            return;
        }
        const action = button.dataset.workflowAction;
        if (action === "cancel") {
            onWorkflowCancel();
        }
    });
    document.getElementById("workflow-regenerate-button").addEventListener("click", () => {
        onWorkflowRegenerate();
    });
    document.getElementById("workflow-regenerate-from-solution-button").addEventListener("click", () => {
        regenerateWorkflowPromptFromSolution().catch((error) => {
            setWorkflowStatus(error.message, "error", "Needs Attention");
            appendWorkflowLog(error.message, "error");
            updateWorkflowActionState();
        });
    });
    document.getElementById("workflow-proceed-button").addEventListener("click", () => {
        onWorkflowProceed();
    });
    document.getElementById("workflow-confirm-styling-button").addEventListener("click", () => {
        onWorkflowConfirmStyling();
    });
    document.getElementById("workflow-keep-plain-button").addEventListener("click", () => {
        onWorkflowKeepPlain();
    });
    document.getElementById("workflow-close-button").addEventListener("click", () => {
        onWorkflowClose();
    });
    document.getElementById("workflow-modal").addEventListener("cancel", (event) => {
        event.preventDefault();
        onWorkflowCancel();
    });
    document.getElementById("learner-band").addEventListener("change", () => {
        applyLearnerBandPreset(true);
    });
    document.getElementById("reveal-mode").addEventListener("change", () => {
        updateDecoyFieldState();
        updateColorPictureControls();
        setFormValidationMessage(validateControlPayloadPreflight(collectControlPayload()));
    });
    document.getElementById("color-picture-source").addEventListener("change", () => {
        updateColorPictureControls();
    });
    document.getElementById("apply-image-styling").addEventListener("change", () => {
        updateImageStylingControls();
        persistPanelState();
    });
    for (const element of form.querySelectorAll("input, select, textarea")) {
        const eventName = element.tagName === "SELECT" ? "change" : "input";
        element.addEventListener(eventName, () => {
            persistPanelState();
        });
    }
    document.getElementById("skill-selection-list").addEventListener("input", () => {
        updateSkillSelectionState();
        updateColorPictureControls();
        persistPanelState();
        setFormValidationMessage(validateControlPayloadPreflight(collectControlPayload()));
    });
    document.getElementById("skill-selection-list").addEventListener("change", () => {
        updateSkillSelectionState();
        updateColorPictureControls();
        persistPanelState();
        setFormValidationMessage(validateControlPayloadPreflight(collectControlPayload()));
    });

    document.getElementById("gallery-search-button").addEventListener("click", () => {
        state.filters.search = document.getElementById("gallery-search-input").value.trim();
        state.filters.learner_band = document.getElementById("gallery-filter-band").value;
        state.filters.reveal_mode = document.getElementById("gallery-filter-mode").value;
        state.filters.skill_profile = document.getElementById("gallery-filter-skill-profile").value;
        state.filters.styling_status = document.getElementById("gallery-filter-styling-status").value;
        state.filters.picture_preset = document.getElementById("gallery-filter-picture-preset").value;
        state.filters.difficulty_minimum = document.getElementById("gallery-filter-difficulty-minimum").value;
        state.filters.difficulty_maximum = document.getElementById("gallery-filter-difficulty-maximum").value;
        state.filters.sort = document.getElementById("gallery-sort-order").value;
        state.filters.offset = 0;
        refreshGallery().catch((error) => setStatus(error.message, "error"));
    });
    document.getElementById("gallery-clear-button").addEventListener("click", () => {
        state.filters = {
            search: "",
            learner_band: "",
            reveal_mode: "",
            skill_profile: "",
            styling_status: "",
            picture_preset: "",
            difficulty_minimum: "",
            difficulty_maximum: "",
            sort: "created_desc",
            offset: 0,
            limit: state.config.worksheet_options.gallery.page_size_default,
        };
        document.getElementById("gallery-search-input").value = "";
        document.getElementById("gallery-filter-band").value = "";
        document.getElementById("gallery-filter-mode").value = "";
        document.getElementById("gallery-filter-skill-profile").value = "";
        document.getElementById("gallery-filter-styling-status").value = "";
        document.getElementById("gallery-filter-picture-preset").value = "";
        document.getElementById("gallery-filter-difficulty-minimum").value = "";
        document.getElementById("gallery-filter-difficulty-maximum").value = "";
        document.getElementById("gallery-sort-order").value = "created_desc";
        refreshGallery().catch((error) => setStatus(error.message, "error"));
    });
    document.getElementById("gallery-load-more-button").addEventListener("click", () => {
        state.filters.offset += state.filters.limit;
        refreshGallery().catch((error) => setStatus(error.message, "error"));
    });

    document.getElementById("modal-close-button").addEventListener("click", () => {
        closeRunModal();
    });
    document.getElementById("modal-confirm-styling-button").addEventListener("click", () => {
        onConfirmStylingDecision();
    });
    document.getElementById("modal-cancel-styling-button").addEventListener("click", () => {
        onCancelStylingDecision();
    });
    document.getElementById("modal-retry-styling-button").addEventListener("click", () => {
        onRetryStylingDecision();
    });
    document.getElementById("modal-retry-generation-button").addEventListener("click", () => {
        onRetryGenerationDecision();
    });
    document.getElementById("image-preview-close-button").addEventListener("click", () => {
        closeImagePreview();
    });
    document.getElementById("maintenance-refresh-button").addEventListener("click", () => {
        onMaintenanceRefresh();
    });
    document.getElementById("maintenance-prune-button").addEventListener("click", () => {
        onMaintenancePrune();
    });
    document.getElementById("maintenance-vacuum-button").addEventListener("click", () => {
        onMaintenanceVacuum();
    });
    document.getElementById("run-modal").addEventListener("cancel", (event) => {
        event.preventDefault();
        closeRunModal();
    });
    window.addEventListener("popstate", () => {
        syncWorkflowModalToLocation({ replaceHistory: true })
            .then((handled) => {
                if (handled) {
                    return;
                }
                return syncRunModalToLocation({ replaceHistory: true });
            })
            .catch((error) => {
            console.error(error);
            setStatus(error.message, "error");
        });
    });
}

async function boot() {
    state.config = await fetchJson("/api/app-config");
    applyDebugUiMode();
    populateSelect(document.getElementById("learner-band"), state.config.worksheet_options.learner_bands);
    populateSelect(document.getElementById("reveal-mode"), state.config.worksheet_options.reveal_modes);
    populateSelect(document.getElementById("reward-style"), state.config.worksheet_options.reward_styles);
    populateSelect(document.getElementById("reward-language"), state.config.worksheet_options.languages);
    renderSkillSelectionControls();
    populatePictureSourceOptions();
    populateSelect(document.getElementById("color-picture-preset"), state.config.worksheet_options.color_picture_presets);
    populateSelect(document.getElementById("image-style-name"), state.config.worksheet_options.image_styling.styles);
    populateSelect(document.getElementById("image-color-mode"), state.config.worksheet_options.image_styling.color_modes);
    document.getElementById("decoy-percentage").value = state.config.worksheet_options.decoy_percentage.default;
    document.getElementById("apply-image-styling").checked = Boolean(state.config.worksheet_options.image_styling.default_enabled);
    document.getElementById("image-style-name").value = state.config.worksheet_options.image_styling.default_style;
    document.getElementById("image-color-mode").value = state.config.worksheet_options.image_styling.default_color_mode;
    document.getElementById("image-ink-saver").checked = Boolean(state.config.worksheet_options.image_styling.default_ink_saver);
    document.getElementById("image-additional-guidance").value = "";
    updateStyleGuidanceStatus({
        countId: "image-additional-guidance-count",
        guidance: document.getElementById("image-additional-guidance").value,
    });
    populateSelect(document.getElementById("gallery-filter-band"), state.config.worksheet_options.learner_bands, { includeBlank: true });
    populateSelect(document.getElementById("gallery-filter-mode"), state.config.worksheet_options.reveal_modes, { includeBlank: true });
    populateSelect(document.getElementById("gallery-filter-skill-profile"), state.config.worksheet_options.skill_profiles, { includeBlank: true });
    populateSelect(document.getElementById("gallery-filter-styling-status"), state.config.worksheet_options.gallery.styling_status_options, { includeBlank: true });
    populateSelect(document.getElementById("gallery-filter-picture-preset"), state.config.worksheet_options.color_picture_presets, { includeBlank: true });
    populateSelect(document.getElementById("gallery-sort-order"), state.config.worksheet_options.gallery.sort_options);
    state.filters.limit = state.config.worksheet_options.gallery.page_size_default;
    const restored = applyPanelState(readPanelState());
    if (!restored) {
        applyLearnerBandPreset(true);
        updateDecoyFieldState();
        updateColorPictureControls();
        updateImageStylingControls();
        persistPanelState();
    }
    document.getElementById("gallery-search-input").disabled = false;
    document.getElementById("gallery-search-button").disabled = false;
    document.getElementById("gallery-clear-button").disabled = false;

    updateGeminiState();
    updateRuntimeMetadata();
    attachEventHandlers();
    updateWorkflowActionState();
    await refreshMaintenance();
    await refreshGallery();
    const restoredWorkflow = await syncWorkflowModalToLocation({ replaceHistory: true });
    if (!restoredWorkflow) {
        await syncRunModalToLocation({ replaceHistory: true });
        setStatus("Ready. Submit the form to open the workflow popup.", "neutral");
    }
}

boot().catch((error) => {
    console.error(error);
    setStatus(error.message, "error");
});
