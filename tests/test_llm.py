"""Provider contract tests never read local credentials or contact a provider."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
from services.llm import configuration, complete_text
from services.ai_answers import LiteLLMAnswerService
from services.dto import CandidateProfile
from services import resume_tailoring as tailoring


def response(content, choices=True):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))] if choices else [])


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.profile = CandidateProfile('Demo', 'User', 'demo@example.com', '', 'Example School')
        env = patch.dict(os.environ, {'AGENT_NAME': 'gemini/test', 'AGENT_API_KEY': 'fake'})
        env.start()
        self.addCleanup(env.stop)
        dotenv = patch('services.llm.dotenv_values', return_value={})
        dotenv.start()
        self.addCleanup(dotenv.stop)

    def test_environment_overrides_dotenv_including_explicit_blank(self):
        with patch('services.llm.dotenv_values', return_value={'AGENT_NAME': 'other', 'AGENT_API_KEY': 'other'}), \
                patch.dict(os.environ, {'AGENT_API_KEY': ''}):
            self.assertEqual(configuration(), ('gemini/test', ''))
            self.assertFalse(LiteLLMAnswerService().is_available())
            with patch('litellm.completion') as provider:
                with self.assertRaises(ValueError):
                    complete_text([])
                provider.assert_not_called()

    def test_answer_uses_generic_configuration_and_exact_option(self):
        with patch('litellm.completion', return_value=response('  yes  ')) as provider:
            answer = LiteLLMAnswerService().suggest_answer('Question', ['Yes', 'No'], self.profile, '')
        self.assertEqual(answer, 'Yes')
        self.assertEqual(provider.call_args.kwargs['model'], 'gemini/test')
        self.assertEqual(provider.call_args.kwargs['api_key'], 'fake')

    def test_declined_empty_and_invalid_choice_are_unmapped(self):
        for content in ('SKIP', '', None, 'invented'):
            with self.subTest(content=content), patch('litellm.completion', return_value=response(content)):
                self.assertIsNone(LiteLLMAnswerService().suggest_answer('Question', ['Yes', 'No'], self.profile, ''))
        with patch('litellm.completion', return_value=response(None, choices=False)):
            self.assertIsNone(LiteLLMAnswerService().suggest_answer('Question', None, self.profile, ''))

    def test_provider_failure_has_redacted_diagnostics(self):
        with patch('litellm.completion', side_effect=RuntimeError('secret credential and resume')), \
                self.assertLogs('services.ai_answers') as logs:
            self.assertIsNone(LiteLLMAnswerService().suggest_answer('Question', None, self.profile, ''))
        self.assertNotIn('secret credential', ''.join(logs.output))
        with patch('litellm.completion', side_effect=RuntimeError('secret credential')):
            with self.assertRaises(tailoring.TailoringError) as error:
                tailoring.rank_resume('job', {})
        self.assertNotIn('secret credential', str(error.exception))

    def test_fenced_json_rankings_supported(self):
        candidates = {key: [tailoring.Block(f'{key}:0', 0, 1, 'x')] for key in tailoring.COUNTS}
        rankings = {key: [f'{key}:0'] for key in candidates}
        with patch('litellm.completion', return_value=response('```json\n'+json.dumps(rankings)+'\n```')):
            self.assertEqual(tailoring.rank_resume('job', candidates), rankings)
