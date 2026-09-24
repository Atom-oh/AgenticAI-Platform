import aws_cdk as cdk
from stack import BankUiuxPlatformStack

app = cdk.App()
BankUiuxPlatformStack(app, "BankUiuxPlatform",
                      env=cdk.Environment(account="180294183052", region="ap-northeast-2"))
app.synth()
