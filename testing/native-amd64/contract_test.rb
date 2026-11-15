# Offline workflow contract: ruby testing/native-amd64/contract_test.rb
require 'minitest/autorun'
require 'yaml'

class NativeAMD64Contract < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SHA = 'f86d86485743b3ee84626b50511495810c79839e'.freeze

  def test_isolated_fixed_source_workflow
    workflow = YAML.load_file(File.join(ROOT, '.github/workflows/native-amd64.yaml'))
    assert_equal({'contents' => 'read'}, workflow.fetch('permissions'))
    assert_equal %w[push workflow_dispatch], workflow.fetch('on').keys
    assert_equal ['ci/plex-kube-vip-f86d864'], workflow['on']['push']['branches']
    assert_equal ['.github/workflows/native-amd64.yaml', 'testing/native-amd64/**'], workflow['on']['push']['paths']
    job = workflow.fetch('jobs').fetch('validate')
    assert_equal 'ubuntu-24.04', job['runs-on']
    assert_includes job['if'], "github.repository == 'chris-cald/kube-vip'"
    assert_includes job['if'], "github.ref == 'refs/heads/ci/plex-kube-vip-f86d864'"
    steps = job.fetch('steps')
    checkouts = steps.select { |s| s.fetch('uses', '').start_with?('actions/checkout@') }
    assert_equal 2, checkouts.length
    assert checkouts.all? { |s| s['with']['persist-credentials'] == false }
    source = checkouts.find { |s| s['with']['path'] == 'source' }
    assert_equal SHA, source['with']['ref']
    assert_equal 'chris-cald/kube-vip', source['with']['repository']
    # Only reuse action pins already present in this revision's workflows.
    existing = Dir[File.join(ROOT, '.github/workflows/*')].reject { |p| p.end_with?('/native-amd64.yaml') }.map { |p| File.read(p) }.join
    steps.map { |s| s['uses'] }.compact.each do |action|
      assert_match(/@[0-9a-f]{40}\z/, action)
      assert_includes existing, action
    end
    upload = steps.find { |s| s.fetch('uses', '').start_with?('actions/upload-artifact@') }
    assert_equal 'always()', upload['if']
    assert_equal 'evidence/', upload['with']['path']
    text = File.read(File.join(ROOT, '.github/workflows/native-amd64.yaml'))
    refute_match(/packages:|id-token:|secrets\.|--push|login-action|setup-qemu|continue-on-error/, text)
    refute_match(/setup-buildx|docker-container|--bootstrap|type=oci|type=docker/, text)
    assert_includes text, 'verify-driver.sh'
    assert_includes text, 'docker buildx build --builder default --load'
    assert_includes text, 'docker image save --output evidence/kube-vip.docker.tar "$IMAGE"'
    assert_includes text, 'BUILDX_METADATA_PROVENANCE: max'
    assert_includes text, '--provenance=false'
    assert_includes text, 'evidence/image-inspect.json'
    report = steps.find { |s| s.fetch('run', '').include?('report.py source evidence') }
    assert_equal 'always()', report['if']
    assert_includes report['run'], '|| status=$?'
    assert_includes report['run'], 'exit "$status"'
    assert_includes text, '--metadata-file evidence/build-metadata.json'
  end
end
