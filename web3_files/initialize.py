from .web3_main import Web3Service
from .web3_contribution import ContributionContractService

web3_service = Web3Service()
contribution_contract_svc = ContributionContractService(web3_service)