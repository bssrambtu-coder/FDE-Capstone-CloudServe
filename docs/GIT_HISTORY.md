# Git history in the submission

The submission source folder contains `FDE_Capstone_Complete.bundle`. It is a
portable Git bundle containing every genuine branch, commit and tag from the
working repository, including the final release commit. It avoids copying loose
`.git` internals while preserving the complete history required by the course.

Verify and clone it with:

```bash
git bundle verify FDE_Capstone_Complete.bundle
git clone FDE_Capstone_Complete.bundle restored-project
```

Commit dates are the dates Git recorded when the work was committed. They were
not rewritten or backdated. The reconstructed 25 August to 10 September plan in
the effort record is planning evidence, not commit-date evidence.
