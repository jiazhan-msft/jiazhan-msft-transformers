# coding=utf-8
# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Testing suite for the PyTorch chameleon model."""

import tempfile
import unittest

import pytest
import requests
from parameterized import parameterized

from transformers import ChameleonConfig, is_torch_available, is_vision_available, set_seed
from transformers.testing_utils import (
    require_bitsandbytes,
    require_flash_attn,
    require_torch,
    require_torch_gpu,
    require_torch_sdpa,
    slow,
    torch_device,
)

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, ids_tensor
from ...test_pipeline_mixin import PipelineTesterMixin


if is_vision_available():
    from PIL import Image

if is_torch_available():
    import torch

    from transformers import (
        ChameleonForCausalLM,
        ChameleonForQuestionAnswering,
        ChameleonForSequenceClassification,
        ChameleonModel,
        ChameleonProcessor,
    )


class ChameleonModelTester:
    def __init__(
        self,
        parent,
        batch_size=13,
        seq_length=7,
        is_training=False,
        use_input_mask=True,
        use_labels=True,
        vocab_size=99,
        image_token_id=98,
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=2,
        intermediate_size=37,
        hidden_act="gelu",
        hidden_dropout_prob=0.1,
        attention_probs_dropout_prob=0.1,
        max_position_embeddings=512,
        type_vocab_size=16,
        type_sequence_label_size=2,
        initializer_range=0.02,
        num_labels=3,
        num_choices=4,
        pad_token_id=0,
        scope=None,
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.use_input_mask = use_input_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.image_token_id = image_token_id
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.num_key_value_heads = num_key_value_heads
        self.intermediate_size = intermediate_size
        self.hidden_act = hidden_act
        self.hidden_dropout_prob = hidden_dropout_prob
        self.attention_probs_dropout_prob = attention_probs_dropout_prob
        self.max_position_embeddings = max_position_embeddings
        self.type_vocab_size = type_vocab_size
        self.type_sequence_label_size = type_sequence_label_size
        self.initializer_range = initializer_range
        self.num_labels = num_labels
        self.num_choices = num_choices
        self.pad_token_id = pad_token_id
        self.scope = scope

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.seq_length], self.vocab_size)

        input_mask = None
        if self.use_input_mask:
            input_mask = torch.tril(torch.ones(self.batch_size, self.seq_length)).to(torch_device)

        sequence_labels = None
        token_labels = None
        choice_labels = None
        if self.use_labels:
            sequence_labels = ids_tensor([self.batch_size], self.type_sequence_label_size)
            token_labels = ids_tensor([self.batch_size, self.seq_length], self.num_labels)
            choice_labels = ids_tensor([self.batch_size], self.num_choices)

        config = self.get_config()

        return config, input_ids, input_mask, sequence_labels, token_labels, choice_labels

    def get_config(self):
        vocab_map = {i: chr(i) for i in range(self.vocab_size)}
        vocab_map[self.image_token_id] = "<image>"
        for i in range(10, 20):
            vocab_map[i] = "IMGIMGBS"
        return ChameleonConfig(
            vocab_size=self.vocab_size,
            hidden_size=self.hidden_size,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            num_key_value_heads=self.num_key_value_heads,
            intermediate_size=self.intermediate_size,
            hidden_act=self.hidden_act,
            hidden_dropout_prob=self.hidden_dropout_prob,
            attention_probs_dropout_prob=self.attention_probs_dropout_prob,
            max_position_embeddings=self.max_position_embeddings,
            type_vocab_size=self.type_vocab_size,
            is_decoder=False,
            initializer_range=self.initializer_range,
            pad_token_id=self.pad_token_id,
            vocabulary_map={v: k for k, v in vocab_map.items()},
            vq_config=self.get_vq_config(),
        )

    def get_vq_config(self):
        return {
            "embed_dim": 24,
            "num_embeddings": 24,
            "z_channels": 12,
            "resolution": 12,
            "in_channels": 3,
            "out_channels": 3,
            "base_channels": 32,  # we have a GroupNorm of 32 groups, so can't do less
            "channel_multiplier": [1, 2],
        }

    def create_and_check_model(self, config, input_ids, input_mask, sequence_labels, token_labels, choice_labels):
        model = ChameleonModel(config=config)
        model.to(torch_device)
        model.eval()
        result = model(input_ids, attention_mask=input_mask)
        result = model(input_ids)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))

    def create_and_check_for_causal_lm(
        self,
        config,
        input_ids,
        input_mask,
        sequence_labels,
        token_labels,
        choice_labels,
        encoder_hidden_states,
        encoder_attention_mask,
    ):
        model = ChameleonForCausalLM(config=config)
        model.to(torch_device)
        model.eval()
        result = model(input_ids, attention_mask=input_mask, labels=token_labels)
        self.parent.assertEqual(result.logits.shape, (self.batch_size, self.seq_length, self.vocab_size))

    def create_and_check_decoder_model_past_large_inputs(
        self,
        config,
        input_ids,
        input_mask,
        sequence_labels,
        token_labels,
        choice_labels,
        encoder_hidden_states,
        encoder_attention_mask,
    ):
        config.is_decoder = True
        model = ChameleonForCausalLM(config=config)
        model.to(torch_device)
        model.eval()

        # first forward pass
        outputs = model(
            input_ids,
            attention_mask=input_mask,
            encoder_hidden_states=encoder_hidden_states,
            encoder_attention_mask=encoder_attention_mask,
            use_cache=True,
        )
        past_key_values = outputs.past_key_values

        # create hypothetical multiple next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 3), config.vocab_size)
        next_mask = ids_tensor((self.batch_size, 3), vocab_size=2)

        # append to next input_ids and
        next_input_ids = torch.cat([input_ids, next_tokens], dim=-1)
        next_attention_mask = torch.cat([input_mask, next_mask], dim=-1)

        output_from_no_past = model(
            next_input_ids,
            attention_mask=next_attention_mask,
            encoder_hidden_states=encoder_hidden_states,
            encoder_attention_mask=encoder_attention_mask,
            output_hidden_states=True,
        )["hidden_states"][0]
        output_from_past = model(
            next_tokens,
            attention_mask=next_attention_mask,
            encoder_hidden_states=encoder_hidden_states,
            encoder_attention_mask=encoder_attention_mask,
            past_key_values=past_key_values,
            output_hidden_states=True,
        )["hidden_states"][0]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].detach()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].detach()

        self.parent.assertTrue(output_from_past_slice.shape[1] == next_tokens.shape[1])

        # test that outputs are equal for slice
        self.parent.assertTrue(torch.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        (
            config,
            input_ids,
            input_mask,
            sequence_labels,
            token_labels,
            choice_labels,
        ) = config_and_inputs
        inputs_dict = {"input_ids": input_ids, "attention_mask": input_mask}
        return config, inputs_dict


@require_torch
class ChameleonModelTest(ModelTesterMixin, GenerationTesterMixin, PipelineTesterMixin, unittest.TestCase):
    all_model_classes = (
        (ChameleonModel, ChameleonForCausalLM, ChameleonForSequenceClassification, ChameleonForQuestionAnswering)
        if is_torch_available()
        else ()
    )
    all_generative_model_classes = (ChameleonForCausalLM,) if is_torch_available() else ()
    pipeline_model_mapping = (
        {
            "feature-extraction": ChameleonModel,
            "text-classification": ChameleonForSequenceClassification,
            "text-generation": ChameleonForCausalLM,
            "zero-shot": ChameleonForSequenceClassification,
            "question-answering": ChameleonForQuestionAnswering,
        }
        if is_torch_available()
        else {}
    )
    test_headmasking = False
    test_pruning = False
    fx_compatible = False

    def setUp(self):
        self.model_tester = ChameleonModelTester(self)
        self.config_tester = ConfigTester(self, config_class=ChameleonConfig, hidden_size=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_chameleon_sequence_classification_model(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.num_labels = 3
        input_ids = input_dict["input_ids"]
        attention_mask = input_ids.ne(1).to(torch_device)
        sequence_labels = ids_tensor([self.model_tester.batch_size], self.model_tester.type_sequence_label_size)
        model = ChameleonForSequenceClassification(config)
        model.to(torch_device)
        model.eval()
        result = model(input_ids, attention_mask=attention_mask, labels=sequence_labels)
        self.assertEqual(result.logits.shape, (self.model_tester.batch_size, self.model_tester.num_labels))

    def test_chameleon_sequence_classification_model_for_single_label(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.num_labels = 3
        config.problem_type = "single_label_classification"
        input_ids = input_dict["input_ids"]
        attention_mask = input_ids.ne(1).to(torch_device)
        sequence_labels = ids_tensor([self.model_tester.batch_size], self.model_tester.type_sequence_label_size)
        model = ChameleonForSequenceClassification(config)
        model.to(torch_device)
        model.eval()
        result = model(input_ids, attention_mask=attention_mask, labels=sequence_labels)
        self.assertEqual(result.logits.shape, (self.model_tester.batch_size, self.model_tester.num_labels))

    def test_chameleon_sequence_classification_model_for_multi_label(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.num_labels = 3
        config.problem_type = "multi_label_classification"
        input_ids = input_dict["input_ids"]
        attention_mask = input_ids.ne(1).to(torch_device)
        sequence_labels = ids_tensor(
            [self.model_tester.batch_size, config.num_labels], self.model_tester.type_sequence_label_size
        ).to(torch.float)
        model = ChameleonForSequenceClassification(config)
        model.to(torch_device)
        model.eval()
        result = model(input_ids, attention_mask=attention_mask, labels=sequence_labels)
        self.assertEqual(result.logits.shape, (self.model_tester.batch_size, self.model_tester.num_labels))

    @parameterized.expand([("linear",), ("dynamic",)])
    def test_model_rope_scaling(self, scaling_type):
        config, _ = self.model_tester.prepare_config_and_inputs_for_common()
        short_input = ids_tensor([1, 10], config.vocab_size)
        long_input = ids_tensor([1, int(config.max_position_embeddings * 1.5)], config.vocab_size)

        set_seed(42)  # Fixed seed at init time so the two models get the same random weights
        original_model = ChameleonModel(config)
        original_model.to(torch_device)
        original_model.eval()
        original_short_output = original_model(short_input).last_hidden_state
        original_long_output = original_model(long_input).last_hidden_state

        set_seed(42)  # Fixed seed at init time so the two models get the same random weights
        config.rope_scaling = {"type": scaling_type, "factor": 10.0}
        scaled_model = ChameleonModel(config)
        scaled_model.to(torch_device)
        scaled_model.eval()
        scaled_short_output = scaled_model(short_input).last_hidden_state
        scaled_long_output = scaled_model(long_input).last_hidden_state

        # Dynamic scaling does not change the RoPE embeddings until it receives an input longer than the original
        # maximum sequence length, so the outputs for the short input should match.
        if scaling_type == "dynamic":
            self.assertTrue(torch.allclose(original_short_output, scaled_short_output, atol=1e-5))
        else:
            self.assertFalse(torch.allclose(original_short_output, scaled_short_output, atol=1e-5))

        # The output should be different for long inputs
        self.assertFalse(torch.allclose(original_long_output, scaled_long_output, atol=1e-5))

    @require_flash_attn
    @require_torch_gpu
    @require_bitsandbytes
    @pytest.mark.flash_attn_test
    @slow
    def test_flash_attn_2_generate_padding_right(self):
        """
        Overwritting the common test as the test is flaky on tiny models
        """
        model = ChameleonForCausalLM.from_pretrained(
            "meta-chameleon/meta-chameleon/chameleon-hf",
            load_in_4bit=True,
            device_map={"": 0},
        )

        processor = ChameleonProcessor.from_pretrained("meta-chameleon/meta-chameleon/chameleon-hf")
        texts = ["hi", "Hello this is a very long sentence"]

        processor.tokenizer.padding_side = "right"

        inputs = processor(texts, return_tensors="pt", padding=True).to(0)

        output_native = model.generate(**inputs, max_new_tokens=20, do_sample=False)
        output_native = processor.tokenizer.batch_decode(output_native)

        model = ChameleonForCausalLM.from_pretrained(
            "meta-chameleon/meta-chameleon/chameleon-hf",
            load_in_4bit=True,
            attn_implementation="flash_attention_2",
        )

        output_fa_2 = model.generate(**inputs, max_new_tokens=20, do_sample=False)
        output_fa_2 = processor.tokenizer.batch_decode(output_fa_2)

        self.assertListEqual(output_native, output_fa_2)

    @require_flash_attn
    @require_torch_gpu
    @slow
    def test_use_flash_attention_2_true(self):
        """
        NOTE: this is the only test testing that the legacy `use_flash_attention=2` argument still works as intended.
        """
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        for model_class in self.all_model_classes:
            with tempfile.TemporaryDirectory() as tmp_dir:
                model = model_class(config)
                model.save_pretrained(tmp_dir)

                new_model = ChameleonForCausalLM.from_pretrained(
                    tmp_dir, use_flash_attention_2=True, torch_dtype=torch.float16
                ).to("cuda")

                self.assertTrue(new_model.config._attn_implementation == "flash_attention_2")

                has_flash = False
                for name, submodule in new_model.named_modules():
                    if "FlashAttention" in submodule.__class__.__name__:
                        has_flash = True
                        break
                if not has_flash:
                    raise ValueError("The flash model should have flash attention layers")

    @require_torch_sdpa
    @slow
    def test_eager_matches_sdpa_generate(self):
        """
        Overwritting the common test as the test is flaky on tiny models
        """
        max_new_tokens = 30

        processor = ChameleonProcessor.from_pretrained("saibo/chameleon-1B")
        model_sdpa = ChameleonForCausalLM.from_pretrained(
            "saibo/chameleon-1B",
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
        ).to(torch_device)

        self.assertTrue(model_sdpa.config._attn_implementation == "sdpa")

        model_eager = ChameleonForCausalLM.from_pretrained(
            "saibo/chameleon-1B",
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            attn_implementation="eager",
        ).to(torch_device)

        self.assertTrue(model_eager.config._attn_implementation == "eager")

        for name, submodule in model_eager.named_modules():
            if "SdpaAttention" in submodule.__class__.__name__:
                raise ValueError("The eager model should not have SDPA attention layers")

        has_sdpa = False
        for name, submodule in model_sdpa.named_modules():
            if "SdpaAttention" in submodule.__class__.__name__:
                has_sdpa = True
                break
        if not has_sdpa:
            raise ValueError("The SDPA model should have SDPA attention layers")

        texts = [
            "hi here's a longer context, getting longer and",
            "Hello this is a very long sentence my friend, very long for real",
            "Today I am in Paris and",
        ]

        for padding_side in ["left", "right"]:
            processor.tokenizer.padding_side = padding_side
            processor.tokenizer.pad_token = processor.tokenizer.eos_token

            inputs = processor(texts, return_tensors="pt", padding=True).to(torch_device)

            res_eager = model_eager.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            res_sdpa = model_sdpa.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

            with self.subTest(f"{padding_side}"):
                torch.testing.assert_close(
                    res_eager,
                    res_sdpa,
                    msg=f"\n{processor.batch_decode(res_eager)} \nvs\n{processor.batch_decode(res_sdpa)}",
                )

    @unittest.skip("Chameleon forces some token ids to be -inf!")
    def test_batching_equivalence(self):
        pass


@require_torch
class ChameleonIntegrationTest(unittest.TestCase):
    @slow
    @require_bitsandbytes
    def test_model_7b(self):
        model = ChameleonForCausalLM.from_pretrained(
            "/raid/raushan/chameleon_rotated_hf", load_in_4bit=True, device_map="auto"
        )
        processor = ChameleonProcessor.from_pretrained("/raid/raushan/chameleon_rotated_hf")

        image = Image.open(
            requests.get("https://nineplanets.org/wp-content/uploads/2020/12/the-big-dipper-1.jpg", stream=True).raw
        )
        prompt = "<image>Describe what do you see here and tell me about the history behind?"

        inputs = processor(prompt, images=image, return_tensors="pt").to(model.device, torch.float16)

        # greedy generation outputs
        EXPECTED_TEXT_COMPLETION = ['Describe what do you see here and tell me about the history behind?The image depicts a star map with a specific constellation highlighted. The constellation is known as the "Southern Cross" and is located in the southern hemisphere. It is a prominent feature of the']  # fmt: skip
        generated_ids = model.generate(**inputs, max_new_tokens=40, do_sample=False)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)
        self.assertEqual(EXPECTED_TEXT_COMPLETION, text)

    @slow
    @require_bitsandbytes
    def test_model_7b_batched(self):
        model = ChameleonForCausalLM.from_pretrained(
            "/raid/raushan/chameleon_rotated_hf", load_in_4bit=True, device_map="auto"
        )
        processor = ChameleonProcessor.from_pretrained("/raid/raushan/chameleon_rotated_hf")

        image = Image.open(
            requests.get("https://nineplanets.org/wp-content/uploads/2020/12/the-big-dipper-1.jpg", stream=True).raw
        )
        image_2 = Image.open(
            requests.get("https://www.kxan.com/wp-content/uploads/sites/40/2020/10/ORION.jpg", stream=True).raw
        )
        prompts = [
            "<image>Describe what do you see here and tell me about the history behind?",
            "What constellation is this image showing?<image>",
        ]

        inputs = processor(prompts, images=[image, image_2], padding=True, return_tensors="pt").to(
            model.device, torch.float16
        )

        # greedy generation outputs
        EXPECTED_TEXT_COMPLETION = [
            'Describe what do you see here and tell me about the history behind?The image depicts a star map with a specific constellation highlighted. The constellation is known as the "Southern Cross" and is located in the southern hemisphere. It is a prominent feature of the',
            'What constellation is this image showing?The image is showing the constellation of Orion.'
            ]  # fmt: skip
        generated_ids = model.generate(**inputs, max_new_tokens=40, do_sample=False)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)
        self.assertEqual(EXPECTED_TEXT_COMPLETION, text)

    @slow
    @require_bitsandbytes
    def test_model_7b_multi_image(self):
        model = ChameleonForCausalLM.from_pretrained(
            "/raid/raushan/chameleon_rotated_hf", load_in_4bit=True, device_map="auto"
        )
        processor = ChameleonProcessor.from_pretrained("/raid/raushan/chameleon_rotated_hf")

        image = Image.open(
            requests.get("https://nineplanets.org/wp-content/uploads/2020/12/the-big-dipper-1.jpg", stream=True).raw
        )
        image_2 = Image.open(
            requests.get("https://www.kxan.com/wp-content/uploads/sites/40/2020/10/ORION.jpg", stream=True).raw
        )
        prompt = "What do these two images have in common?<image><image>"

        inputs = processor(prompt, images=[image, image_2], return_tensors="pt").to(model.device, torch.float16)

        # greedy generation outputs
        EXPECTED_TEXT_COMPLETION = ['What do these two images have in common?The two images show a connection between two things that are not necessarily related. The first image shows a group of stars, while the second image shows a network of lines connecting two points. The connection between']  # fmt: skip
        generated_ids = model.generate(**inputs, max_new_tokens=40, do_sample=False)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)
        self.assertEqual(EXPECTED_TEXT_COMPLETION, text)
