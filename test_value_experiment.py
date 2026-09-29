import unittest
from harness.value_experiment import adoption_decision

class AdoptionGateTests(unittest.TestCase):
    def test_synthetic_success_cannot_promote(self):
        verdict=adoption_decision({'evidenceKind':'synthetic-handoff-smoke','allTrialsComplete':True,'allAccountingComplete':True})
        self.assertFalse(verdict['automaticPromotion']);self.assertIn('not-independent-maintenance-evidence',verdict['reasons'])
    def test_missing_costs_prevent_promotion(self):
        verdict=adoption_decision({'evidenceKind':'independent-maintenance-trial','allTrialsComplete':True,'allAccountingComplete':False})
        self.assertFalse(verdict['automaticPromotion']);self.assertIn('total-resource-accounting-incomplete',verdict['reasons'])
    def test_completed_table_is_not_generalization_certificate(self):
        verdict=adoption_decision({'evidenceKind':'independent-maintenance-trial','allTrialsComplete':True,'allAccountingComplete':True})
        self.assertFalse(verdict['automaticPromotion']);self.assertIn('no-reviewed-generalization-and-adoption-contract',verdict['reasons'])
if __name__=='__main__':unittest.main()
