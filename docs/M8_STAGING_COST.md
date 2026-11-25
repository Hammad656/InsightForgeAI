# Staging cost worksheet

Public rates reviewed November 25, 2026 for US East (N. Virginia). USD, on demand, 730 hours per month, no credits, taxes, or volume discounts. The portfolio case runs all four ECS services for 80 hours and leaves the infrastructure allocated. The continuous case runs them for 730 hours and budgets for a Multi-AZ database and a second cache node as a future topology change.

| Charge | Portfolio | Continuous |
|---|---:|---:|
| Fargate Linux x86, 2 vCPU and 4 GiB total | $7.90 | $72.08 |
| Internal ALB, 730 hours plus 10 / 100 LCU-hours | $16.51 | $17.23 |
| Four PrivateLink endpoints across two AZs plus 5 / 50 GB processed | $58.45 | $58.90 |
| S3 Standard storage, 2 / 20 GB | $0.05 | $0.46 |
| CloudWatch application log ingestion, 2 / 20 GB | $1.00 | $10.00 |
| Secrets Manager storage, five secret-months including RDS master | $2.00 | $2.00 |
| NAT | $0 | $0 |
| LLM calls with the configured disabled provider | $0 | $0 |
| Core network, compute and service subtotal | $85.91 | $160.67 |

Rates come from [AWS Fargate and ALB cost examples](https://docs.aws.amazon.com/solutions/latest/prebid-server-deployment-on-aws/cost1.html), [AWS PrivateLink rates](https://aws.amazon.com/privatelink/pricing/), [S3 and Secrets Manager rates](https://docs.aws.amazon.com/solutions/latest/automated-security-response-on-aws/cost.html), and [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/).

## Remaining charges and modeled totals

| Charge and assumption | Portfolio | Continuous |
|---|---:|---:|
| PostgreSQL db.t4g.micro, 730 h at $0.016 Single-AZ / $0.032 Multi-AZ | $11.68 | $23.36 |
| PostgreSQL gp3, 20 GB-month at $0.115 / $0.23 | $2.30 | $4.60 |
| Redis OSS cache.t4g.micro, 730 / 1,460 node-hours at $0.016 | $11.68 | $23.36 |
| Retained or excess backups, RDS 5 / 20 GB at $0.095, Redis 1 / 5 GB at $0.085 | $0.56 | $2.33 |
| ECR, 2 / 5 GB-month at $0.10 | $0.20 | $0.50 |
| S3 at $0.005/1k writes and $0.0004/1k reads, 1k / 10k writes and 5k / 50k reads | $0.01 | $0.07 |
| One KMS key, 3k / 30k requests, plus 2k / 20k Secrets Manager API requests | $1.02 | $1.19 |
| CloudWatch retained logs and Container Insights budgeting, 2 / 20 stored GB and 0.5 / 5 additional ingested GB | $0.31 | $3.10 |
| 11 CloudWatch custom metrics and nine standard alarms | $4.20 | $4.20 |
| Client VPN, one / two 730-hour associations at $0.10 | $73.00 | $146.00 |
| Client VPN sessions and their public IPv4 addresses, 20 / 80 connection hours | $1.10 | $4.40 |
| One Route 53 private zone, existing domain and non-exportable ACM certificate | $0.50 | $0.50 |
| Data transfer budget, 10 / 100 billed regional GB at $0.01 plus 5 / 50 egress GB at $0.09 | $0.55 | $5.50 |
| **Modeled monthly total, model disabled** | **$193.02** | **$379.78** |

Totals use unrounded line items. They are scenario estimates, not account quotes. They assume an existing registered domain and identity provider, standard SNS notifications with negligible usage, no CPU credit overage, no extra gp3 IOPS, and no temporary restore instance kept running. Continuous Multi-AZ and replica assumptions are priced upgrades to the checked-in single-AZ defaults. VPN associations, ALB, endpoints, RDS and cache bill while allocated, even when ECS is stopped. Reuse an approved existing VPN when available to avoid new association charges.

Regional rates verified against the public [RDS catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/us-east-1/index.csv), [ElastiCache catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonElastiCache/current/us-east-1/index.csv), and [VPC catalog](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/us-east-1/index.csv). Other rates use [ECR](https://aws.amazon.com/ecr/pricing/), [S3](https://aws.amazon.com/s3/pricing/), [KMS](https://aws.amazon.com/kms/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) and [Route 53](https://aws.amazon.com/route53/pricing/) pricing.

Optional model-only illustration at the published GPT-4.1 mini rates of $0.40 per million input and $1.60 per million output tokens is $0.56 for 1M input/100k output and $5.60 for 10M input/1M output. OpenAI labels that model deprecated. Reprice a supported model and add the approved proxy, egress, and secret costs before enabling live AI. [OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

Scale ECS service counts to zero outside the demo window. This saves task-hours while the load balancer, endpoints, database, and cache continue to incur charges. Before an approved shutdown, export required artifacts, verify the latest recoverable backup, drain tasks and queues, and review which persistent resources and snapshots to retain. Set a budget notification before deployment and confirm the post-shutdown billable inventory.
