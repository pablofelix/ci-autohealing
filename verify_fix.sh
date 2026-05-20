#!/bin/bash
# Verify error extraction fix results

echo "=================================================="
echo "Error Extraction Fix Verification"
echo "=================================================="
echo ""

echo "1. Distribution of failed steps (AFTER fix):"
echo "--------------------------------------------------"
./ic db query "
SELECT
    failed_step_name,
    COUNT(*) as count
FROM build_failures
WHERE application = 'acme-v2-1-ea-1'
  AND status = 'Failed'
GROUP BY failed_step_name
ORDER BY count DESC
"

echo ""
echo "2. Distribution of error types (AFTER fix):"
echo "--------------------------------------------------"
./ic db query "
SELECT
    error_type,
    COUNT(*) as count
FROM build_failures
WHERE application = 'acme-v2-1-ea-1'
  AND status = 'Failed'
GROUP BY error_type
ORDER BY count DESC
"

echo ""
echo "3. Sample of improved error messages:"
echo "--------------------------------------------------"
./ic db query "
SELECT
    component_name,
    failed_step_name,
    error_type,
    LEFT(error_message, 80) as error_preview
FROM build_failures
WHERE application = 'acme-v2-1-ea-1'
  AND status = 'Failed'
LIMIT 5
"

echo ""
echo "4. Components still showing 'init-task' (should be 0):"
echo "--------------------------------------------------"
./ic db query "
SELECT COUNT(*) as incorrect_count
FROM build_failures
WHERE application = 'acme-v2-1-ea-1'
  AND status = 'Failed'
  AND failed_step_name = 'init-task'
"

echo ""
echo "5. Components with CONTEXT error (should be 0):"
echo "--------------------------------------------------"
./ic db query "
SELECT COUNT(*) as context_error_count
FROM build_failures
WHERE application = 'acme-v2-1-ea-1'
  AND status = 'Failed'
  AND error_message LIKE '%CONTEXT parameter%'
"

echo ""
echo "=================================================="
echo "Verification Complete"
echo "=================================================="
