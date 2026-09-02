import aws_cdk as cdk
from stack import HanaUiuxPlatformStack

app = cdk.App()
HanaUiuxPlatformStack(app, "HanaUiuxPlatform",
                      env=cdk.Environment(account="180294183052", region="ap-northeast-2"))
app.synth()
