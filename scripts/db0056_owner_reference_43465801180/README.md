# DB0056 portable owner reference

[GitHub Actions run 43465801180](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/43465801180)
retained two separate fresh PostgreSQL measurements from commit
`7461b4de830f6f6fa6d430093714b47303a1b709`. The two files are the original
role-redacted inherited-catalog receipt bytes from artifacts `14059009371` and
`14058774762`, respectively. Both 24-case diagnostic suites passed.

The reference verifies the portable pre-grant inherited definitions and the
normalized fitted owner catalog. It also checks that the real pre-grant
read-only and read-write definitions agree with the post-grant inherited
definitions in each cluster. The two raw fitted owner catalog digests differ.
Each target must measure its own raw owner catalogs. A match leaves grant,
startup and execution admission denied.
