# server/models/vision_olmocr.py — OlmOCR-2 Math Vision Model
# Phase P2-1: replaces Tesseract for math frames.
#
# One job:
#   Take one PIL.Image (a frame from the live capture)
#   Return clean LaTeX text — math wrapped properly, plain text plain
#
# Why a separate class instead of inlining in the endpoint:
#   The model is heavy — ~16 GB on the GPU. It must load ONCE at server
#   start, not per-request. Wrapping it in a class with a load() method
#   matches transcriber.py's pattern and lets main.py warm it on lifespan.
#
# Why NOT replacing Tesseract entirely:
#   Tesseract still wins on plain English paragraphs — faster, no GPU.
#   OlmOCR-2 is reserved for math-heavy frames where Tesseract fails.
#   The endpoint chooses which path to call.

import time
import torch

# Loading these here at module level, NOT inside the class — lets the
# import fail cleanly at server boot if transformers/PIL are missing
# instead of crashing the first inference call.
try:
    from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
    OLMOCR_DEPS_OK = True
except ImportError as e:
    print(f"[VISION_V2] transformers not available: {e}")
    OLMOCR_DEPS_OK = False


# ============================================
# MODEL ID — pinned to the exact HF repo we tested
# ============================================
# Pinned at the version we validated end-to-end on May 09 2026.
# If a newer OlmOCR version drops, bump this and re-run the smoke test
# before flipping the live server.
OLMOCR_MODEL_ID = "allenai/olmOCR-7B-0225-preview"


# ============================================
# OCR PROMPT — kept short and surgical
# ============================================
# Ask only what we need for the live overlay path:
#   - extract every visible character
#   - output LaTeX-wrapped math, plain-text words
# The bigger "rewrite as one minimal paragraph" prompt from PLAN_2.md §10
# is for the session-end DeepSeek pass, NOT here. This stays raw and fast.
OCR_PROMPT = (
    "Read every character visible in this image. "
    "Wrap inline math in $...$ and display math in $$...$$. "
    "Keep every symbol, variable, and equation exactly as shown. "
    "Output only the extracted text — no commentary."
)


class OlmOCRModel:
    """
    Wraps OlmOCR-2 (Qwen2-VL-based) for single-frame math OCR.

    Lifecycle:
        construct → load() once at server startup → ocr_image(img) per request
    """

    def __init__(self):
        # name is for log lines and the /vision_v2/health response
        self.name = "OlmOCR-7B-0225-preview"
        # processor handles tokenization + image preprocessing
        # model is the actual neural net weights
        self.processor = None
        self.model = None
        # tracks whether load() ran successfully — endpoints check this
        # before accepting requests, returns 503 if False
        self.ready = False

    def load(self):
        """
        Load weights from HF cache on /parallel_scratch into the L40S.

        First load on a cold filesystem is ~7 minutes. Warm BeeGFS
        cache drops it to ~10 seconds. Either way it runs once per
        Slurm job, never per request.
        """
        if not OLMOCR_DEPS_OK:
            raise RuntimeError(
                "transformers/PIL missing — cannot load OlmOCR. "
                "Check the server requirements.txt was installed."
            )

        print(f"[MODEL] Loading {self.name}...")
        t0 = time.time()

        # processor first — tokenizer + image preprocessor in one object.
        # Comes from the same HF repo as the weights.
        self.processor = AutoProcessor.from_pretrained(OLMOCR_MODEL_ID)

        # model load — fp16 fits comfortably in L40S 48 GB alongside
        # Whisper + Canary. device_map="cuda" pushes weights straight
        # to GPU instead of CPU-then-move (saves ~30 sec on cold load).
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            OLMOCR_MODEL_ID,
            dtype=torch.float16,
            device_map="cuda",
        )

        # eval mode — turns off gradient tracking and dropout layers.
        # Inference only. Saves memory and speeds up forward pass.
        self.model.eval()

        self.ready = True
        print(f"[MODEL] {self.name} ready in {time.time() - t0:.1f}s")

    def ocr_image(self, image, max_new_tokens: int = 512) -> str:
        """
        Run OCR on one frame. Returns the LaTeX-wrapped text.

        Args:
            image: PIL.Image (any mode — converted to RGB internally)
            max_new_tokens: cap on generation length. 512 covers a
                            full whiteboard with several equations.
                            Bump higher if frames are dense.

        Returns:
            extracted text string with math wrapped per OCR_PROMPT.
            Empty string if the model produces no output.
        """
        if not self.ready:
            raise RuntimeError(f"{self.name} not loaded — call load() first")

        # convert to RGB — Qwen2-VL expects 3-channel input.
        # Skipping this on palette PNGs causes a silent quality hit
        # (model sees garbled colors, hallucinates symbols).
        if image.mode != "RGB":
            image = image.convert("RGB")

        # build the chat-format input: one user turn, image + text.
        # apply_chat_template wraps it in the special <|image_pad|>
        # tokens the model was fine-tuned to expect.
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text",  "text":  OCR_PROMPT},
            ],
        }]
        text_input = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

        # processor() does both tokenization (text → ids) and image
        # preprocessing (resize, normalize, patchify) in one call.
        # padding=True pads to the longest sequence in the batch
        # (we send one image at a time, so it's a no-op here).
        inputs = self.processor(
            text=[text_input],
            images=[image],
            padding=True,
            return_tensors="pt",
        ).to("cuda")

        # local cuDNN enable for the conv3d patches in the vision tower.
        # transcriber.py disables cuDNN globally because Canary works
        # without it — but Qwen2-VL's patch_embed REQUIRES conv3d which
        # only works with cuDNN. Toggle on for this call, off after.
        prev_cudnn = torch.backends.cudnn.enabled
        torch.backends.cudnn.enabled = True
        try:
            # do_sample=False = greedy decoding. Math OCR wants the
            # deterministic best-guess token, not a creative sample.
            # max_new_tokens caps runaway generation on weird inputs.
            with torch.no_grad():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                )
        finally:
            # always restore — even if generate() raises, the global
            # setting must come back to False so audio inference is
            # not affected.
            torch.backends.cudnn.enabled = prev_cudnn

        # trim the prompt tokens off the front of the output. generate()
        # returns input + generated, but we only want the generated half.
        prompt_len = inputs.input_ids.shape[1]
        new_tokens = output_ids[:, prompt_len:]

        # decode token ids back to a string. skip_special_tokens=True
        # drops <|im_end|>, <|endoftext|>, etc. so the caller gets
        # only the LaTeX-wrapped text.
        text = self.processor.batch_decode(
            new_tokens, skip_special_tokens=True,
        )[0]

        return text.strip()
